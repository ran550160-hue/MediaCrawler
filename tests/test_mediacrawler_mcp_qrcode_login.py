from __future__ import annotations

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
        status = manager.get_qrcode_login_status(result["login_task_id"])
        assert status["status"] == "waiting_scan"
    finally:
        manager.cancel_qrcode_login(result["login_task_id"])

    deadline = time.time() + 3
    while current_xhs_profile_owner() is not None and time.time() < deadline:
        time.sleep(0.02)
    assert current_xhs_profile_owner() is None


def test_start_qrcode_login_rejects_when_profile_busy(tmp_path):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")
    owner = "test:qrcode-busy"
    assert acquire_xhs_profile(owner) is True
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
    while current_xhs_profile_owner() is not None and time.time() < deadline:
        time.sleep(0.02)
    assert current_xhs_profile_owner() is None


def test_qrcode_login_rejects_unsupported_platform(tmp_path):
    storage = _storage(tmp_path)
    manager = QRCodeLoginManager(storage, repo_root=tmp_path / "repo")

    with pytest.raises(McpAppError) as exc_info:
        manager.start_qrcode_login(platform="dy")

    assert exc_info.value.code == ErrorCode.UNSUPPORTED_PLATFORM
