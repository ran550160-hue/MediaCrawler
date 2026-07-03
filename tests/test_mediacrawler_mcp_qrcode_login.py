from __future__ import annotations

import asyncio
from pathlib import Path
import threading
import time

import pytest

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.locks import acquire_xhs_profile, current_xhs_profile_owner, release_xhs_profile
from mediacrawler_mcp.qrcode_login import QRCodeLoginManager
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.utils import utc_now_iso


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
        self.page = _FakePage(selector_failures=selector_failures)
        self.cleared = False
        self.closed = False

    async def new_page(self):
        return self.page

    async def cookies(self):
        return list(self._cookies)

    async def clear_cookies(self):
        self.cleared = True
        self._cookies = []

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

    def fake_start_background_worker(**kwargs):
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
        )
        threading.Thread(
            target=lambda: (kwargs["cancel_event"].wait(2), release_xhs_profile(kwargs["lock_owner"])),
            daemon=True,
        ).start()

    monkeypatch.setattr(manager, "_start_background_worker", fake_start_background_worker)

    result = manager.start_qrcode_login(qr_wait_seconds=1)

    try:
        assert result["status"] == "waiting_scan"
        assert result["login_task_id"].startswith("task_qrcode_login_xhs_")
        assert Path(result["qr_image_path"]).exists()
        assert result["qr_ready"] is True
        assert result["qr_image_exists"] is True
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

    def fake_start_background_worker(**kwargs):
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
        )
        threading.Thread(
            target=lambda: (kwargs["cancel_event"].wait(2), release_xhs_profile(kwargs["lock_owner"])),
            daemon=True,
        ).start()

    monkeypatch.setattr(manager, "_start_background_worker", fake_start_background_worker)

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
            cancel_event=threading.Event(),
        )
    )

    result = manager.get_qrcode_login_status("login-valid")
    assert result["status"] == "success"
    assert result["qr_ready"] is False
    assert result["qr_image_exists"] is False
    assert context.cleared is False
    assert storage.get_account_row("xhs:default")["status"] == "logged_in"


def test_qrcode_login_clears_stale_initial_profile_cookie(tmp_path, monkeypatch):
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
            cancel_event=threading.Event(),
        )
    )

    result = manager.get_qrcode_login_status("login-stale")
    assert result["status"] == "expired"
    assert result["qr_ready"] is False
    assert result["qr_image_exists"] is True
    assert context.cleared is True
    assert context.page.evaluated is True
    assert storage.get_account_row("xhs:default") is None


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
            cancel_event=threading.Event(),
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
