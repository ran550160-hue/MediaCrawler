from __future__ import annotations

import asyncio
from pathlib import Path
import time

import pytest

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.locks import acquire_xhs_profile, current_xhs_profile_owner, release_xhs_profile
from mediacrawler_mcp.login_manager import LoginManager
from mediacrawler_mcp.qrcode_login import QRCodeLoginManager
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.utils import utc_now_iso


class _FakeProcess:
    pid = 9876


class _FakeQrElement:
    async def screenshot(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"png")


class _FakeLocator:
    async def click(self, timeout):
        return None


class _FakePage:
    def __init__(self, selector_failures: int = 0):
        self.evaluated = False
        self.reloads = 0
        self.screenshots: list[str] = []
        self.url = "https://www.xiaohongshu.com/login"
        self.selector_failures = selector_failures

    async def goto(self, url, wait_until):
        self.url = url
        return None

    async def wait_for_selector(self, selector, timeout):
        if self.selector_failures > 0:
            self.selector_failures -= 1
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError

            raise PlaywrightTimeoutError("QR not ready")
        return _FakeQrElement()

    def locator(self, selector):
        return _FakeLocator()

    async def evaluate(self, script):
        self.evaluated = True

    async def reload(self, wait_until):
        self.reloads += 1

    async def screenshot(self, path, full_page=True):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"error")
        self.screenshots.append(str(path))


class _FakeContext:
    def __init__(self, cookies, selector_failures: int = 0):
        self._cookies = cookies
        self._cookie_batches = cookies if cookies and isinstance(cookies[0], list) else None
        self._cookie_calls = 0
        self.page = _FakePage(selector_failures=selector_failures)
        self.cleared = False
        self.cleared_cookie_names: list[str | None] = []
        self.closed = False
        self.init_script_paths: list[str] = []
        self.init_scripts: list[str] = []
        self.events: list[str] = []

    async def new_page(self):
        self.events.append("new_page")
        return self.page

    async def cookies(self):
        if self._cookie_batches is not None:
            batch = self._cookie_batches[min(self._cookie_calls, len(self._cookie_batches) - 1)]
            self._cookie_calls += 1
            return list(batch)
        return list(self._cookies)

    async def clear_cookies(self, **kwargs):
        name = kwargs.get("name")
        self.cleared_cookie_names.append(name)
        if name == "web_session":
            self._cookies = [cookie for cookie in self._cookies if cookie.get("name") != "web_session"]
            if self._cookie_batches is not None:
                self._cookie_batches = [
                    [cookie for cookie in batch if cookie.get("name") != "web_session"]
                    for batch in self._cookie_batches
                ]
            return
        self.cleared = True
        self._cookies = []
        self._cookie_batches = None

    async def add_init_script(self, script=None, *, path=None):
        self.events.append("add_init_script")
        if path is not None:
            self.init_script_paths.append(str(path))
        if script is not None:
            self.init_scripts.append(script)

    async def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, context):
        self.context = context

    async def launch_persistent_context(self, **kwargs):
        return self.context


class _FakePlaywright:
    def __init__(self, context):
        self.chromium = _FakeChromium(context)


class _FakeAsyncPlaywright:
    def __init__(self, context):
        self.context = context

    async def __aenter__(self):
        return _FakePlaywright(self.context)

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _storage(tmp_path):
    config = McpConfig(
        home=tmp_path / "mcp_home",
        browser_mode="persistent_context",
        cdp_endpoint=None,
        max_concurrent_tasks=1,
        default_timeout_seconds=300,
    )
    return Storage(config)


def test_start_qrcode_login_returns_qr_path_and_status(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")

    def fake_start_worker_process(**kwargs):
        Path(kwargs["qr_image_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["qr_image_path"]).write_bytes(b"png")
        row = storage.get_login_session_row(kwargs["login_task_id"])
        storage.upsert_login_session(
            login_session_id=kwargs["login_task_id"],
            platform="xhs",
            account_name=kwargs["account_name"],
            status="waiting_scan",
            qr_image_path=str(kwargs["qr_image_path"]),
            profile_dir=str(kwargs["profile_dir"]),
            expires_at=kwargs["expires_at"],
            message="QR ready",
            created_at=row["created_at"],
            updated_at=utc_now_iso(),
            pid=_FakeProcess.pid,
            worker_log_path=str(kwargs["worker_log_path"]),
        )
        return _FakeProcess()

    monkeypatch.setattr(manager, "_start_worker_process", fake_start_worker_process)

    result = manager.start_qrcode_login(qr_wait_seconds=1)

    try:
        assert result["status"] == "waiting_scan"
        assert result["login_task_id"].startswith("task_qrcode_login_xhs_")
        assert result["qr_image_path"].endswith("_qr_1.png")
        assert Path(result["qr_image_path"]).exists()
        assert result["qr_ready"] is True
        assert result["qr_image_exists"] is True
        assert result["worker_pid"] == _FakeProcess.pid
        status = manager.get_qrcode_login_status(result["login_task_id"])
        assert status["status"] == "waiting_scan"
        assert status["qr_ready"] is True
    finally:
        manager.cancel_qrcode_login(result["login_task_id"])

    deadline = time.time() + 3
    while current_xhs_profile_owner(storage.config) is not None and time.time() < deadline:
        time.sleep(0.02)
    assert current_xhs_profile_owner(storage.config) is None


def test_start_qrcode_login_rejects_when_profile_busy(tmp_path):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")
    owner = "test:qrcode-busy"
    assert acquire_xhs_profile(storage.config, owner) is True
    try:
        with pytest.raises(McpAppError) as exc_info:
            manager.start_qrcode_login(qr_wait_seconds=1)

        assert exc_info.value.code == ErrorCode.RESOURCE_BUSY
    finally:
        release_xhs_profile(owner)


def test_cancel_qrcode_login_marks_session_cancelled_and_releases_lock(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")

    def fake_start_worker_process(**kwargs):
        row = storage.get_login_session_row(kwargs["login_task_id"])
        storage.upsert_login_session(
            login_session_id=kwargs["login_task_id"],
            platform="xhs",
            account_name=kwargs["account_name"],
            status="waiting_scan",
            qr_image_path=str(kwargs["qr_image_path"]),
            profile_dir=str(kwargs["profile_dir"]),
            expires_at=kwargs["expires_at"],
            message="QR ready",
            created_at=row["created_at"],
            updated_at=utc_now_iso(),
            pid=_FakeProcess.pid,
            worker_log_path=str(kwargs["worker_log_path"]),
        )
        return _FakeProcess()

    monkeypatch.setattr(manager, "_start_worker_process", fake_start_worker_process)
    monkeypatch.setattr(manager, "_terminate_worker", lambda pid: None)

    started = manager.start_qrcode_login(qr_wait_seconds=1)
    cancelled = manager.cancel_qrcode_login(started["login_task_id"])

    assert cancelled["status"] == "cancelled"
    deadline = time.time() + 3
    while current_xhs_profile_owner(storage.config) is not None and time.time() < deadline:
        time.sleep(0.02)
    assert current_xhs_profile_owner(storage.config) is None


def test_qrcode_login_rejects_unsupported_platform(tmp_path):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")

    with pytest.raises(McpAppError) as exc_info:
        manager.start_qrcode_login(platform="dy")

    assert exc_info.value.code == ErrorCode.UNSUPPORTED_PLATFORM


def test_get_qrcode_login_status_repairs_waiting_scan_when_cookie_verified(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")
    LoginManager(storage, repo_root=tmp_path / "repo").import_cookies("xhs", "web_session=fresh; a=b")
    monkeypatch.setattr(LoginManager, "_verify_xhs_cookie_remote", lambda self, cookie: True)
    now = utc_now_iso()
    storage.upsert_login_session(
        login_session_id="task_qrcode_login_xhs_repair",
        platform="xhs",
        account_name="default",
        status="waiting_scan",
        qr_image_path=str(storage.config.login_qrcodes_dir / "repair.png"),
        profile_dir=str(tmp_path / "repo" / "browser_data" / "xhs_user_data_dir"),
        expires_at="2099-01-01T00:00:00+00:00",
        message="QR ready",
        created_at=now,
        updated_at=now,
        pid=999999,
    )

    result = manager.get_qrcode_login_status("task_qrcode_login_xhs_repair")

    assert result["status"] == "success"
    assert "repaired" in result["message"]


def test_qrcode_login_accepts_initial_cookie_only_after_remote_verify(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")
    context = _FakeContext([{"name": "web_session", "value": "fresh-session"}])
    monkeypatch.setattr("mediacrawler_mcp.qrcode_login.async_playwright", lambda: _FakeAsyncPlaywright(context))
    monkeypatch.setattr(manager, "_verify_cookie_remote", lambda cookie: True)

    asyncio.run(
        manager._run_qrcode_login(
            login_task_id="login-valid",
            account_name="default",
            qr_image_path=storage.config.login_qrcodes_dir / "login-valid.png",
            profile_dir=tmp_path / "repo" / "browser_data" / "xhs_user_data_dir",
            expires_at="2099-01-01T00:00:00+00:00",
            timeout_seconds=0,
            headless=True,
        )
    )

    result = manager.get_qrcode_login_status("login-valid")
    assert result["status"] == "success"
    assert result["qr_ready"] is False
    assert result["qr_image_exists"] is False
    assert context.cleared is False
    assert storage.get_account_row("xhs:default")["status"] == "logged_in"


def test_qrcode_login_clears_only_stale_initial_login_cookie(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")
    context = _FakeContext([{"name": "web_session", "value": "stale-session"}])
    monkeypatch.setattr("mediacrawler_mcp.qrcode_login.async_playwright", lambda: _FakeAsyncPlaywright(context))
    monkeypatch.setattr(manager, "_verify_cookie_remote", lambda cookie: False)

    asyncio.run(
        manager._run_qrcode_login(
            login_task_id="login-stale",
            account_name="default",
            qr_image_path=storage.config.login_qrcodes_dir / "login-stale.png",
            profile_dir=tmp_path / "repo" / "browser_data" / "xhs_user_data_dir",
            expires_at="2099-01-01T00:00:00+00:00",
            timeout_seconds=0,
            headless=True,
        )
    )

    result = manager.get_qrcode_login_status("login-stale")
    assert result["status"] == "expired"
    assert result["qr_ready"] is False
    assert result["qr_image_exists"] is True
    assert context.cleared is False
    assert context.cleared_cookie_names == ["web_session"]
    assert context.page.evaluated is False
    assert storage.get_account_row("xhs:default") is None


def test_qrcode_login_adds_stealth_and_mac_fingerprint_scripts_before_opening_page(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    repo_root = tmp_path / "repo"
    stealth_path = repo_root / "libs" / "stealth.min.js"
    stealth_path.parent.mkdir(parents=True)
    stealth_path.write_text("// stealth", encoding="utf-8")
    manager = QRCodeLoginManager(storage, repo_root=repo_root)
    context = _FakeContext([])
    monkeypatch.setattr("mediacrawler_mcp.qrcode_login.async_playwright", lambda: _FakeAsyncPlaywright(context))

    asyncio.run(
        manager._run_qrcode_login(
            login_task_id="login-stealth",
            account_name="default",
            qr_image_path=storage.config.login_qrcodes_dir / "login-stealth.png",
            profile_dir=repo_root / "browser_data" / "xhs_user_data_dir",
            expires_at="2099-01-01T00:00:00+00:00",
            timeout_seconds=0,
            headless=True,
        )
    )

    assert context.init_script_paths == [str(stealth_path)]
    assert len(context.init_scripts) == 1
    fingerprint_script = context.init_scripts[0]
    assert "MacIntel" in fingerprint_script
    assert "'zh-CN', 'zh', 'en'" in fingerprint_script
    assert "platform: 'macOS'" in fingerprint_script
    assert "userAgentData" in fingerprint_script
    assert "webdriver" in fingerprint_script
    assert context.events[:3] == ["add_init_script", "add_init_script", "new_page"]


def test_qrcode_login_fails_with_debug_screenshot_when_qr_not_found(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")
    context = _FakeContext([], selector_failures=3)
    monkeypatch.setattr("mediacrawler_mcp.qrcode_login.async_playwright", lambda: _FakeAsyncPlaywright(context))

    asyncio.run(
        manager._run_qrcode_login(
            login_task_id="login-no-qr",
            account_name="default",
            qr_image_path=storage.config.login_qrcodes_dir / "login-no-qr.png",
            profile_dir=tmp_path / "repo" / "browser_data" / "xhs_user_data_dir",
            expires_at="2099-01-01T00:00:00+00:00",
            timeout_seconds=30,
            headless=True,
        )
    )

    result = manager.get_qrcode_login_status("login-no-qr")
    error_path = storage.config.login_qrcodes_dir / "login-no-qr_error.png"
    assert result["status"] == "failed"
    assert result["qr_ready"] is False
    assert result["qr_image_exists"] is False
    assert context.page.reloads == 1
    assert error_path.exists()
    assert str(error_path) in result["message"]
    assert "current_url=https://www.xiaohongshu.com" in result["message"]


def test_qrcode_login_retries_observed_cookie_then_succeeds_without_clearing(tmp_path, monkeypatch, capsys):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")
    manager.REMOTE_VERIFY_WINDOW_SECONDS = 1
    manager.REMOTE_VERIFY_INTERVAL_SECONDS = 0.01
    manager.XHS_COOKIE_SETTLE_SECONDS = 0.01
    manager.XHS_COOKIE_SETTLE_SAMPLE_SECONDS = 0.01
    context = _FakeContext(
        [
            [],
            [{"name": "web_session", "value": "fresh-session"}, {"name": "id_token", "value": "token"}],
            [{"name": "web_session", "value": "fresh-session"}, {"name": "id_token", "value": "token"}],
            [{"name": "web_session", "value": "fresh-session"}, {"name": "id_token", "value": "token"}],
        ]
    )
    monkeypatch.setattr("mediacrawler_mcp.qrcode_login.async_playwright", lambda: _FakeAsyncPlaywright(context))
    results = [
        {
            "ok": False,
            "status": "remote_verify_failed",
            "error_code": ErrorCode.REMOTE_VERIFY_FAILED,
            "message": "not stable yet",
            "http_status": 200,
            "xhs_code": -1,
            "xhs_msg": "not stable yet",
        },
        {
            "ok": True,
            "status": "logged_in",
            "error_code": None,
            "message": "ok",
            "http_status": 200,
            "xhs_code": 0,
            "xhs_msg": "success",
        },
    ]
    monkeypatch.setattr(manager, "_verify_cookie_remote", lambda cookie: results.pop(0))

    asyncio.run(
        manager._run_qrcode_login(
            login_task_id="login-observed-success",
            account_name="default",
            qr_image_path=storage.config.login_qrcodes_dir / "login-observed-success_qr_1.png",
            profile_dir=tmp_path / "repo" / "browser_data" / "xhs_user_data_dir",
            expires_at="2099-01-01T00:00:00+00:00",
            timeout_seconds=2,
            headless=True,
        )
    )

    result = manager.get_qrcode_login_status("login-observed-success")
    captured = capsys.readouterr().out
    assert result["status"] == "success"
    assert result["verification_attempts"] == 2
    assert result["last_verify_error_code"] is None
    assert result["last_verify_message"] is None
    assert result["observed_cookie_at"] is not None
    assert context.cleared is False
    assert result["qr_image_path"].endswith("_qr_1.png")
    assert "web_session observed" in captured
    assert "remote verify failed" in captured
    assert "remote verify passed" in captured
    assert "fresh-session" not in captured
    assert "id_token=token" not in captured


def test_qrcode_login_observed_cookie_failure_becomes_terminal(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")
    manager.REMOTE_VERIFY_WINDOW_SECONDS = 0.05
    manager.REMOTE_VERIFY_INTERVAL_SECONDS = 0.01
    manager.XHS_COOKIE_SETTLE_SECONDS = 0.01
    manager.XHS_COOKIE_SETTLE_SAMPLE_SECONDS = 0.01
    context = _FakeContext(
        [
            [],
            [{"name": "web_session", "value": "bad-session"}],
            [{"name": "web_session", "value": "bad-session"}],
        ]
    )
    monkeypatch.setattr("mediacrawler_mcp.qrcode_login.async_playwright", lambda: _FakeAsyncPlaywright(context))
    monkeypatch.setattr(
        manager,
        "_verify_cookie_remote",
        lambda cookie: {
            "ok": False,
            "status": "remote_verify_failed",
            "error_code": ErrorCode.REMOTE_VERIFY_FAILED,
            "message": "selfinfo failed",
            "http_status": 200,
            "xhs_code": -1,
            "xhs_msg": "selfinfo failed",
        },
    )

    asyncio.run(
        manager._run_qrcode_login(
            login_task_id="login-observed-failed",
            account_name="default",
            qr_image_path=storage.config.login_qrcodes_dir / "login-observed-failed_qr_1.png",
            profile_dir=tmp_path / "repo" / "browser_data" / "xhs_user_data_dir",
            expires_at="2099-01-01T00:00:00+00:00",
            timeout_seconds=2,
            headless=True,
        )
    )

    result = manager.get_qrcode_login_status("login-observed-failed")
    assert result["status"] == "remote_verify_failed"
    assert result["error_code"] == ErrorCode.REMOTE_VERIFY_FAILED
    assert result["last_verify_error_code"] == ErrorCode.REMOTE_VERIFY_FAILED
    assert result["verification_attempts"] >= 1
    assert context.cleared is False
    assert storage.get_account_row("xhs:default") is None


def test_qrcode_login_observed_cookie_permission_denied_becomes_terminal(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")
    manager.REMOTE_VERIFY_WINDOW_SECONDS = 0.05
    manager.REMOTE_VERIFY_INTERVAL_SECONDS = 0.01
    manager.XHS_COOKIE_SETTLE_SECONDS = 0.01
    manager.XHS_COOKIE_SETTLE_SAMPLE_SECONDS = 0.01
    context = _FakeContext(
        [
            [],
            [{"name": "web_session", "value": "denied-session"}, {"name": "id_token", "value": "token"}],
        ]
    )
    monkeypatch.setattr("mediacrawler_mcp.qrcode_login.async_playwright", lambda: _FakeAsyncPlaywright(context))
    monkeypatch.setattr(
        manager,
        "_verify_cookie_remote",
        lambda cookie: {
            "ok": False,
            "status": "permission_denied",
            "error_code": ErrorCode.XHS_PERMISSION_DENIED,
            "message": "您当前登录的账号没有权限访问",
            "http_status": 200,
            "xhs_code": -1,
            "xhs_msg": "您当前登录的账号没有权限访问",
        },
    )

    asyncio.run(
        manager._run_qrcode_login(
            login_task_id="login-permission-denied",
            account_name="default",
            qr_image_path=storage.config.login_qrcodes_dir / "login-permission-denied_qr_1.png",
            profile_dir=tmp_path / "repo" / "browser_data" / "xhs_user_data_dir",
            expires_at="2099-01-01T00:00:00+00:00",
            timeout_seconds=2,
            headless=True,
        )
    )

    result = manager.get_qrcode_login_status("login-permission-denied")
    assert result["status"] == "permission_denied"
    assert result["error_code"] == ErrorCode.XHS_PERMISSION_DENIED
    assert result["last_verify_error_code"] == ErrorCode.XHS_PERMISSION_DENIED
    assert result["verification_attempts"] == 1
    assert context.cleared is False


def test_qrcode_login_permission_denied_with_incomplete_cookie_retries(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")
    manager.REMOTE_VERIFY_WINDOW_SECONDS = 0.25
    manager.REMOTE_VERIFY_INTERVAL_SECONDS = 0.01
    manager.XHS_COOKIE_SETTLE_SECONDS = 0.01
    manager.XHS_COOKIE_SETTLE_SAMPLE_SECONDS = 0.01
    context = _FakeContext([[], [{"name": "web_session", "value": "denied-session"}]])
    monkeypatch.setattr("mediacrawler_mcp.qrcode_login.async_playwright", lambda: _FakeAsyncPlaywright(context))
    monkeypatch.setattr(
        manager,
        "_verify_cookie_remote",
        lambda cookie: {
            "ok": False,
            "status": "permission_denied",
            "error_code": ErrorCode.XHS_PERMISSION_DENIED,
            "message": "您当前登录的账号没有权限访问",
            "http_status": 200,
            "xhs_code": -104,
            "xhs_msg": "您当前登录的账号没有权限访问",
        },
    )

    asyncio.run(
        manager._run_qrcode_login(
            login_task_id="login-permission-incomplete",
            account_name="default",
            qr_image_path=storage.config.login_qrcodes_dir / "login-permission-incomplete_qr_1.png",
            profile_dir=tmp_path / "repo" / "browser_data" / "xhs_user_data_dir",
            expires_at="2099-01-01T00:00:00+00:00",
            timeout_seconds=2,
            headless=True,
        )
    )

    result = manager.get_qrcode_login_status("login-permission-incomplete")
    assert result["status"] == "permission_denied"
    assert result["error_code"] == ErrorCode.XHS_PERMISSION_DENIED
    assert result["verification_attempts"] > 1
    assert context.cleared is False


def test_qrcode_login_waits_for_settled_cookie_names_before_remote_verify(tmp_path, monkeypatch, capsys):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")
    manager.REMOTE_VERIFY_WINDOW_SECONDS = 0.5
    manager.REMOTE_VERIFY_INTERVAL_SECONDS = 0.01
    manager.XHS_COOKIE_SETTLE_SECONDS = 0.2
    manager.XHS_COOKIE_SETTLE_SAMPLE_SECONDS = 0.01
    context = _FakeContext(
        [
            [],
            [{"name": "web_session", "value": "fresh-session"}],
            [{"name": "web_session", "value": "fresh-session"}],
            [{"name": "web_session", "value": "fresh-session"}, {"name": "id_token", "value": "token"}],
            [{"name": "web_session", "value": "fresh-session"}, {"name": "id_token", "value": "token"}],
        ]
    )
    monkeypatch.setattr("mediacrawler_mcp.qrcode_login.async_playwright", lambda: _FakeAsyncPlaywright(context))
    verified_cookies: list[str] = []

    def fake_verify(cookie: str):
        verified_cookies.append(cookie)
        return {
            "ok": True,
            "status": "logged_in",
            "error_code": None,
            "message": "ok",
            "http_status": 200,
            "xhs_code": 0,
            "xhs_msg": "success",
        }

    monkeypatch.setattr(manager, "_verify_cookie_remote", fake_verify)

    asyncio.run(
        manager._run_qrcode_login(
            login_task_id="login-settled-cookie",
            account_name="default",
            qr_image_path=storage.config.login_qrcodes_dir / "login-settled-cookie_qr_1.png",
            profile_dir=tmp_path / "repo" / "browser_data" / "xhs_user_data_dir",
            expires_at="2099-01-01T00:00:00+00:00",
            timeout_seconds=2,
            headless=True,
        )
    )

    result = manager.get_qrcode_login_status("login-settled-cookie")
    captured = capsys.readouterr().out
    assert result["status"] == "success"
    assert len(verified_cookies) == 1
    assert "id_token=token" in verified_cookies[0]
    assert "waiting for settled cookie snapshot" in captured
    assert "fresh-session" not in captured
    assert "id_token=token" not in captured


def test_qrcode_login_expiry_grace_observes_delayed_cookie(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")
    manager.QR_CONFIRM_GRACE_SECONDS = 0.2
    manager.QR_POLL_INTERVAL_SECONDS = 0.01
    manager.REMOTE_VERIFY_WINDOW_SECONDS = 0.2
    manager.REMOTE_VERIFY_INTERVAL_SECONDS = 0.01
    manager.XHS_COOKIE_SETTLE_SECONDS = 0.01
    manager.XHS_COOKIE_SETTLE_SAMPLE_SECONDS = 0.01
    storage.initialize()
    now = utc_now_iso()
    storage.upsert_login_session(
        login_session_id="login-grace",
        platform="xhs",
        account_name="default",
        status="waiting_scan",
        qr_image_path=str(storage.config.login_qrcodes_dir / "login-grace_qr_1.png"),
        profile_dir=str(tmp_path / "repo" / "browser_data" / "xhs_user_data_dir"),
        expires_at="2000-01-01T00:00:00+00:00",
        message="QR ready",
        created_at=now,
        updated_at=now,
    )
    context = _FakeContext([[], [{"name": "web_session", "value": "delayed-session"}]])
    monkeypatch.setattr(manager, "_verify_cookie_remote", lambda cookie: True)

    observed = asyncio.run(
        manager._observe_cookie_after_qr_expiry(
            context=context,
            login_task_id="login-grace",
            account_name="default",
            profile_dir=tmp_path / "repo" / "browser_data" / "xhs_user_data_dir",
            expires_at="2000-01-01T00:00:00+00:00",
            qr_image_path=storage.config.login_qrcodes_dir / "login-grace_qr_1.png",
        )
    )

    result = manager.get_qrcode_login_status("login-grace")
    assert observed is True
    assert result["status"] == "success"
    assert result["observed_cookie_at"] is not None
