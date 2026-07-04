from __future__ import annotations

import inspect

from mediacrawler_mcp import server


def test_start_collection_schema_defaults_to_remote_verify():
    signature = inspect.signature(server.start_collection)

    assert signature.parameters["verify_login_remote"].default is True
    assert signature.parameters["skip_preflight"].default is False


def test_get_login_status_schema_exposes_permission_verify():
    signature = inspect.signature(server.get_login_status)

    assert signature.parameters["verify_permission"].default is False


def test_start_qrcode_login_schema_exposes_qr_wait_seconds(monkeypatch):
    calls = {}

    class FakeQRCodeLoginManager:
        def __init__(self, storage):
            self.storage = storage

        def start_qrcode_login(self, **kwargs):
            calls.update(kwargs)
            return {"status": "waiting_scan"}

    monkeypatch.setattr(server, "QRCodeLoginManager", FakeQRCodeLoginManager)

    result = server.start_qrcode_login(qr_wait_seconds=7)

    assert result["status"] == "waiting_scan"
    assert calls["qr_wait_seconds"] == 7
