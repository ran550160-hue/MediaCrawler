from __future__ import annotations

import pytest

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.login_manager import LoginManager
from mediacrawler_mcp.storage import Storage


def _storage(tmp_path):
    config = McpConfig(
        home=tmp_path / "mcp_home",
        browser_mode="persistent_context",
        cdp_endpoint=None,
        max_concurrent_tasks=1,
        default_timeout_seconds=300,
    )
    return Storage(config)


def test_get_login_status_returns_logged_out_without_cookie_or_profile(tmp_path):
    storage = _storage(tmp_path)
    manager = LoginManager(storage, repo_root=tmp_path / "repo")

    status = manager.get_login_status("xhs")

    assert status["status"] == "logged_out"
    assert status["platform"] == "xhs"
    assert "import_cookies" in status["message"]
    account = storage.get_account_row("xhs:default")
    assert account is not None
    assert account["status"] == "logged_out"


def test_get_login_status_detects_local_browser_profile(tmp_path):
    storage = _storage(tmp_path)
    profile_dir = tmp_path / "repo" / "browser_data" / "xhs_user_data_dir"
    profile_dir.mkdir(parents=True)
    (profile_dir / "Default").mkdir()
    manager = LoginManager(storage, repo_root=tmp_path / "repo")

    status = manager.get_login_status("xhs")

    assert status["status"] == "logged_in"
    assert status["login_source"] == "browser_profile"
    assert status["profile_dir"] == str(profile_dir)
    assert storage.get_account_row("xhs:default")["status"] == "logged_in"


def test_import_cookies_stores_cookie_and_account_record(tmp_path):
    storage = _storage(tmp_path)
    manager = LoginManager(storage, repo_root=tmp_path / "repo")

    result = manager.import_cookies("xhs", "a=b; web_session=session-value; c=d")

    assert result["status"] == "success"
    cookie_path = storage.config.accounts_dir / "xhs" / "default" / "cookies.txt"
    assert cookie_path.exists()
    assert cookie_path.read_text(encoding="utf-8").strip() == "a=b; web_session=session-value; c=d"
    assert result["cookie_file_path"] == str(cookie_path)
    assert "session-value" not in result["message"]

    account = storage.get_account_row("xhs:default")
    assert account is not None
    assert account["status"] == "logged_in"

    status = manager.get_login_status("xhs")
    assert status["status"] == "logged_in"
    assert status["login_source"] == "cookie"
    assert manager.get_cookie_string("xhs") == "a=b; web_session=session-value; c=d"


def test_import_cookies_rejects_missing_web_session(tmp_path):
    storage = _storage(tmp_path)
    manager = LoginManager(storage, repo_root=tmp_path / "repo")

    with pytest.raises(McpAppError) as exc_info:
        manager.import_cookies("xhs", "a=b; other=value")

    assert exc_info.value.code == ErrorCode.INVALID_ARGUMENT
    assert "web_session" in exc_info.value.message


def test_login_manager_rejects_unsupported_platform(tmp_path):
    storage = _storage(tmp_path)
    manager = LoginManager(storage, repo_root=tmp_path / "repo")

    with pytest.raises(McpAppError) as exc_info:
        manager.get_login_status("dy")

    assert exc_info.value.code == ErrorCode.UNSUPPORTED_PLATFORM
