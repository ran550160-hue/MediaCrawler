import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import api.main as api_main
import api.routers.agent as agent_router
import api.routers.browser as browser_router
import api.routers.data as data_router
from api.main import app
from api.schemas import CrawlerStartRequest, PlatformEnum
from api.services.crawler_manager import CrawlerManager


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def datetime_from_file(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


def test_crawler_manager_build_command_includes_runtime_options():
    cm = CrawlerManager()
    request = CrawlerStartRequest(
        platform=PlatformEnum.XHS,
        keywords="test",
        enable_cdp_mode=False,
        cdp_connect_existing=False,
        cdp_debug_port=9333,
        save_data_path="custom-data",
        enable_ip_proxy=True,
        ip_proxy_provider_name="static",
        static_proxy_url="http://user:pass@127.0.0.1:7890",
    )

    cmd = cm._build_command(request)

    assert cmd[cmd.index("--enable_cdp_mode") + 1] == "false"
    assert cmd[cmd.index("--cdp_connect_existing") + 1] == "false"
    assert cmd[cmd.index("--cdp_debug_port") + 1] == "9333"
    assert cmd[cmd.index("--save_data_path") + 1] == "custom-data"
    assert cmd[cmd.index("--enable_ip_proxy") + 1] == "true"
    assert cmd[cmd.index("--ip_proxy_provider_name") + 1] == "static"
    assert cmd[cmd.index("--static_proxy_url") + 1] == "http://user:pass@127.0.0.1:7890"


def test_api_start_accepts_runtime_options():
    client = TestClient(app)

    with patch("api.routers.crawler.crawler_manager.start", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = True

        response = client.post(
            "/api/crawler/start",
            json={
                "platform": "xhs",
                "login_type": "qrcode",
                "crawler_type": "search",
                "keywords": "test",
                "enable_cdp_mode": False,
                "cdp_connect_existing": False,
                "cdp_debug_port": 9333,
                "save_data_path": "custom-data",
                "enable_ip_proxy": True,
                "ip_proxy_provider_name": "static",
                "static_proxy_url": "http://127.0.0.1:7890",
            },
        )

    assert response.status_code == 200
    called_request = mock_start.call_args[0][0]
    assert called_request.enable_cdp_mode is False
    assert called_request.cdp_connect_existing is False
    assert called_request.cdp_debug_port == 9333
    assert called_request.save_data_path == "custom-data"
    assert called_request.enable_ip_proxy is True
    assert called_request.ip_proxy_provider_name == "static"
    assert called_request.static_proxy_url == "http://127.0.0.1:7890"


def test_api_start_rejects_invalid_cdp_port():
    client = TestClient(app)
    with patch("api.routers.crawler.crawler_manager.start", new_callable=AsyncMock) as mock_start:
        response = client.post(
            "/api/crawler/start",
            json={
                "platform": "xhs",
                "login_type": "qrcode",
                "crawler_type": "search",
                "keywords": "test",
                "cdp_debug_port": 70000,
            },
        )

    assert response.status_code == 422
    mock_start.assert_not_called()


def test_data_api_lists_and_previews_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(data_router, "DATA_DIR", tmp_path)
    _write_jsonl(
        tmp_path / "xhs" / "jsonl" / "search_contents_2026-07-07.jsonl",
        [{"note_id": "n1", "title": "first"}, {"note_id": "n2", "title": "second"}],
    )
    client = TestClient(app)

    files_response = client.get("/api/data/files", params={"file_type": "jsonl"})
    preview_response = client.get(
        "/api/data/files/xhs/jsonl/search_contents_2026-07-07.jsonl",
        params={"preview": True, "limit": 1},
    )

    assert files_response.status_code == 200
    files = files_response.json()["files"]
    assert len(files) == 1
    assert files[0]["type"] == "jsonl"
    assert files[0]["record_count"] == 2
    assert preview_response.status_code == 200
    assert preview_response.json()["total"] == 2
    assert preview_response.json()["data"] == [{"note_id": "n1", "title": "first"}]


def test_data_api_rejects_invalid_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(data_router, "DATA_DIR", tmp_path)
    bad_file = tmp_path / "xhs" / "jsonl" / "bad.jsonl"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text('{"ok": true}\nnot json\n', encoding="utf-8")
    client = TestClient(app)

    response = client.get("/api/data/files/xhs/jsonl/bad.jsonl", params={"preview": True})

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid JSONL file at line 2"


def test_env_check_returns_structured_checks(monkeypatch):
    async def fake_run_command(command, timeout=10.0):
        if command == ["uv", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout=b"uv 0.8.0", stderr=b"")
        if command == ["uv", "run", "main.py", "--help"]:
            return subprocess.CompletedProcess(command, 0, stdout=b"usage: main.py", stderr=b"")
        if command == ["node", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout=b"v20.0.0", stderr=b"")
        raise AssertionError(command)

    monkeypatch.setattr(api_main, "_run_command", fake_run_command)
    monkeypatch.setattr(
        api_main,
        "_check_cdp_port",
        lambda port: {"name": "chrome_cdp", "status": "warning", "message": "not reachable"},
    )
    client = TestClient(app)

    response = client.get("/api/env/check")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert {check["name"] for check in payload["checks"]} >= {
        "uv",
        "mediacrawler",
        "node",
        "chrome_cdp",
        "data_dir",
        "webui",
    }
    assert payload["output"] == "usage: main.py"


def test_env_check_reports_missing_uv(monkeypatch):
    async def fake_run_command(command, timeout=10.0):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(api_main, "_run_command", fake_run_command)
    monkeypatch.setattr(
        api_main,
        "_check_cdp_port",
        lambda port: {"name": "chrome_cdp", "status": "warning", "message": "not reachable"},
    )
    client = TestClient(app)

    response = client.get("/api/env/check")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["checks"][0]["name"] == "uv"
    assert payload["checks"][0]["status"] == "error"


def test_export_dataset_bundle_api_uses_local_jsonl(tmp_path):
    data_root = tmp_path / "data"
    output_dir = tmp_path / "datasets"
    jsonl_dir = data_root / "xhs" / "jsonl"
    _write_jsonl(jsonl_dir / "search_contents_2026-07-07.jsonl", [{"note_id": "n1", "title": "first"}])
    _write_jsonl(jsonl_dir / "search_comments_2026-07-07.jsonl", [{"note_id": "n1", "comment_id": "c1"}])
    client = TestClient(app)

    response = client.post(
        "/api/datasets/export",
        json={
            "name": "API Bundle",
            "platform": "xhs",
            "crawler_type": "search",
            "keywords": ["api"],
            "data_root": str(data_root),
            "output_dir": str(output_dir),
            "dataset_id": "api_bundle",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    bundle_dir = output_dir / "api_bundle"
    manifest = json.loads((bundle_dir / "dataset.json").read_text(encoding="utf-8"))
    assert payload["status"] == "success"
    assert payload["dataset_id"] == "api_bundle"
    assert payload["metrics"] == {"content_count": 1, "comment_count": 1}
    assert manifest["dataset_id"] == "api_bundle"


def test_export_dataset_bundle_api_rejects_non_xhs(tmp_path):
    client = TestClient(app)

    response = client.post(
        "/api/datasets/export",
        json={
            "name": "Bad Bundle",
            "platform": "dy",
            "crawler_type": "search",
            "keywords": ["api"],
            "output_dir": str(tmp_path / "datasets"),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Dataset export currently supports platform=xhs only"


def test_cdp_browser_status_reports_reachable(monkeypatch):
    monkeypatch.setattr(browser_router, "_is_port_open", lambda port: True)
    monkeypatch.setattr(
        browser_router,
        "_get_cdp_version",
        lambda port: {"Browser": "Chrome/Test", "webSocketDebuggerUrl": "ws://test"},
    )
    client = TestClient(app)

    response = client.get("/api/browser/cdp/status", params={"port": 9222})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["reachable"] is True
    assert payload["browser"] == "Chrome/Test"


def test_cdp_browser_start_launches_detected_browser(monkeypatch, tmp_path):
    calls = {}

    class FakeProcess:
        pid = 12345

        def poll(self):
            return None

    class FakeLauncher:
        def __init__(self):
            self.browser_process = None

        def detect_browser_paths(self):
            return [str(tmp_path / "chrome.exe")]

        def find_available_port(self, start_port):
            return start_port

        def launch_browser(self, browser_path, debug_port, headless, user_data_dir, debug_address="127.0.0.1"):
            calls.update(
                {
                    "browser_path": browser_path,
                    "debug_port": debug_port,
                    "headless": headless,
                    "user_data_dir": user_data_dir,
                }
            )
            self.browser_process = FakeProcess()
            return self.browser_process

        def wait_for_browser_ready(self, debug_port, timeout):
            return True

        def cleanup(self):
            calls["cleanup"] = True

    monkeypatch.setattr(browser_router, "_active_launcher", None)
    monkeypatch.setattr(browser_router, "_active_port", None)
    monkeypatch.setattr(browser_router, "_is_port_open", lambda port: False)
    monkeypatch.setattr(browser_router, "_get_cdp_version", lambda port: {})
    monkeypatch.setattr(browser_router, "BrowserLauncher", FakeLauncher)
    client = TestClient(app)

    response = client.post(
        "/api/browser/cdp/start",
        json={
            "port": 9333,
            "headless": True,
            "user_data_dir": str(tmp_path / "profile"),
            "timeout_seconds": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "started"
    assert payload["port"] == 9333
    assert payload["pid"] == 12345
    assert calls["debug_port"] == 9333
    assert calls["headless"] is True
    assert calls["user_data_dir"] == str(tmp_path / "profile")


def test_cdp_browser_start_uses_default_xhs_profile(monkeypatch, tmp_path):
    calls = {}
    default_profile = tmp_path / "browser_data" / "xhs_cdp_profile"

    class FakeProcess:
        pid = 23456

        def poll(self):
            return None

    class FakeLauncher:
        def __init__(self):
            self.browser_process = None

        def detect_browser_paths(self):
            return [str(tmp_path / "chrome.exe")]

        def find_available_port(self, start_port):
            return start_port

        def launch_browser(self, browser_path, debug_port, headless, user_data_dir, debug_address="127.0.0.1"):
            calls["user_data_dir"] = user_data_dir
            self.browser_process = FakeProcess()
            return self.browser_process

        def wait_for_browser_ready(self, debug_port, timeout):
            return True

        def cleanup(self):
            pass

    monkeypatch.setattr(browser_router, "DEFAULT_CDP_USER_DATA_DIR", default_profile)
    monkeypatch.setattr(browser_router, "_active_launcher", None)
    monkeypatch.setattr(browser_router, "_active_port", None)
    monkeypatch.setattr(browser_router, "_active_user_data_dir", None)
    monkeypatch.setattr(browser_router, "_is_port_open", lambda port: False)
    monkeypatch.setattr(browser_router, "_get_cdp_version", lambda port: {})
    monkeypatch.setattr(browser_router, "BrowserLauncher", FakeLauncher)
    client = TestClient(app)

    response = client.post("/api/browser/cdp/start", json={"port": 9222})

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_data_dir"] == str(default_profile)
    assert calls["user_data_dir"] == str(default_profile)


def test_cdp_browser_open_opens_url_in_existing_browser(monkeypatch):
    calls = {}
    monkeypatch.setattr(browser_router, "_is_port_open", lambda port: True)
    monkeypatch.setattr(browser_router, "_get_cdp_version", lambda port: {})
    monkeypatch.setattr(
        browser_router,
        "_open_cdp_url",
        lambda port, url: calls.update({"port": port, "url": url}) or {"id": "page-1"},
    )
    client = TestClient(app)

    response = client.post(
        "/api/browser/cdp/open",
        json={"port": 9222, "url": "https://www.xiaohongshu.com/explore"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "opened"
    assert calls == {"port": 9222, "url": "https://www.xiaohongshu.com/explore"}


def test_agent_xhs_search_starts_restricted_task(tmp_path, monkeypatch):
    async def fake_start(request):
        calls["request"] = request
        return True

    calls = {}
    agent_router._tasks.clear()
    monkeypatch.setattr(agent_router, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(agent_router.crawler_manager, "process", None)
    monkeypatch.setattr(agent_router.crawler_manager, "start", fake_start)
    client = TestClient(app)

    response = client.post(
        "/api/agent/xhs/search",
        json={
            "keywords": ["AI编程副业", "程序员接单"],
            "max_contents": 12,
            "max_comments_per_content": 4,
            "include_comments": True,
            "cdp_debug_port": 9333,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["task_id"].startswith("agent_xhs_")
    request = calls["request"]
    assert request.platform.value == "xhs"
    assert request.crawler_type.value == "search"
    assert request.save_option.value == "jsonl"
    assert request.max_notes_count == 12
    assert request.max_comments_count == 4
    assert request.cdp_debug_port == 9333
    assert request.save_data_path.startswith(str((tmp_path / "data" / "agent_runs").resolve()))


def test_agent_xhs_search_requires_token_when_configured(monkeypatch):
    monkeypatch.setenv("MEDIACRAWLER_AGENT_TOKEN", "secret")
    client = TestClient(app)

    response = client.post(
        "/api/agent/xhs/search",
        json={"keywords": ["AI"], "max_contents": 1},
    )

    assert response.status_code == 401


def test_agent_task_status_and_finalize_export_bundle(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    task_data_root = data_root / "agent_runs" / "agent_xhs_test"
    jsonl_dir = task_data_root / "xhs" / "jsonl"
    contents_path = jsonl_dir / "search_contents_2026-07-08.jsonl"
    comments_path = jsonl_dir / "search_comments_2026-07-08.jsonl"
    _write_jsonl(contents_path, [{"note_id": "n1", "title": "first"}])
    _write_jsonl(comments_path, [{"note_id": "n1", "comment_id": "c1", "content": "hello"}])
    _write_jsonl(
        data_root / "xhs" / "jsonl" / "search_contents_2026-07-08.jsonl",
        [{"note_id": "football", "title": "历史足球数据"}],
    )
    monkeypatch.setattr(agent_router, "DATA_DIR", data_root)
    monkeypatch.setattr(data_router, "DATA_DIR", data_root)
    agent_router._tasks.clear()
    task_id = "agent_xhs_test"
    agent_router._tasks[task_id] = {
        "task_id": task_id,
        "status": "completed",
        "keywords": ["AI"],
        "request": {},
        "dataset_name": "Agent Test",
        "description": "",
        "data_root": str(task_data_root),
        "started_at": datetime_from_file(contents_path),
        "completed_at": datetime_from_file(contents_path),
        "exit_code": 0,
        "message": "done",
    }
    monkeypatch.setattr(agent_router, "_recent_logs", lambda limit=30: [])
    client = TestClient(app)

    status_response = client.get(f"/api/agent/tasks/{task_id}")
    finalize_response = client.post(
        f"/api/agent/tasks/{task_id}/finalize",
        json={
            "dataset_name": "Agent Test",
            "output_dir": str(tmp_path / "datasets"),
            "dataset_id": "agent_bundle",
        },
    )

    assert status_response.status_code == 200
    assert status_response.json()["can_finalize"] is True
    assert finalize_response.status_code == 200
    payload = finalize_response.json()
    assert payload["dataset_id"] == "agent_bundle"
    assert payload["metrics"] == {"content_count": 1, "comment_count": 1}
    assert payload["partial"] is False
    assert (tmp_path / "datasets" / "agent_bundle" / "dataset.json").exists()
    exported_contents = (tmp_path / "datasets" / "agent_bundle" / "raw" / "xhs_contents.jsonl").read_text(encoding="utf-8")
    assert "football" not in exported_contents


def test_agent_running_task_with_output_can_partial_finalize(tmp_path, monkeypatch):
    async def fake_stop():
        calls.append("stop")
        return True

    class FakeProcess:
        def poll(self):
            return None

    calls = []
    data_root = tmp_path / "data"
    task_data_root = data_root / "agent_runs" / "agent_xhs_partial"
    jsonl_dir = task_data_root / "xhs" / "jsonl"
    _write_jsonl(jsonl_dir / "search_contents_2026-07-08.jsonl", [{"note_id": "n1", "title": "AI"}])
    monkeypatch.setattr(agent_router, "DATA_DIR", data_root)
    monkeypatch.setattr(data_router, "DATA_DIR", data_root)
    monkeypatch.setattr(agent_router.crawler_manager, "process", FakeProcess())
    monkeypatch.setattr(agent_router.crawler_manager, "status", "running")
    monkeypatch.setattr(agent_router.crawler_manager, "last_exit_code", None)
    monkeypatch.setattr(agent_router.crawler_manager, "stop", fake_stop)
    agent_router._tasks.clear()
    agent_router._current_task_id = "agent_xhs_partial"
    agent_router._tasks["agent_xhs_partial"] = {
        "task_id": "agent_xhs_partial",
        "status": "running",
        "keywords": ["AI"],
        "request": {"keywords": ["AI"], "max_contents": 20, "include_comments": False},
        "dataset_name": "Partial Agent",
        "description": "",
        "data_root": str(task_data_root),
        "started_at": datetime.now(),
        "completed_at": None,
        "exit_code": None,
        "message": "running",
    }
    client = TestClient(app)

    status_response = client.get("/api/agent/tasks/agent_xhs_partial")
    finalize_response = client.post(
        "/api/agent/tasks/agent_xhs_partial/finalize",
        json={"output_dir": str(tmp_path / "datasets"), "dataset_id": "partial_bundle"},
    )

    assert status_response.status_code == 200
    assert status_response.json()["can_finalize"] is True
    assert status_response.json()["progress"]["contents_count"] == 1
    assert finalize_response.status_code == 200
    assert finalize_response.json()["partial"] is True
    assert calls == ["stop"]


def test_agent_cancelled_task_with_output_can_finalize(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    task_data_root = data_root / "agent_runs" / "agent_xhs_cancelled"
    jsonl_dir = task_data_root / "xhs" / "jsonl"
    _write_jsonl(jsonl_dir / "search_contents_2026-07-08.jsonl", [{"note_id": "n1", "title": "AI"}])
    monkeypatch.setattr(agent_router, "DATA_DIR", data_root)
    monkeypatch.setattr(data_router, "DATA_DIR", data_root)
    agent_router._tasks.clear()
    agent_router._current_task_id = None
    agent_router._tasks["agent_xhs_cancelled"] = {
        "task_id": "agent_xhs_cancelled",
        "status": "cancelled",
        "keywords": ["AI"],
        "request": {"keywords": ["AI"], "max_contents": 20, "include_comments": False},
        "dataset_name": "Cancelled Agent",
        "description": "",
        "data_root": str(task_data_root),
        "started_at": datetime.now(),
        "completed_at": datetime.now(),
        "exit_code": None,
        "message": "cancelled",
    }
    client = TestClient(app)

    status_response = client.get("/api/agent/tasks/agent_xhs_cancelled")
    finalize_response = client.post(
        "/api/agent/tasks/agent_xhs_cancelled/finalize",
        json={"output_dir": str(tmp_path / "datasets"), "dataset_id": "cancelled_bundle"},
    )

    assert status_response.status_code == 200
    assert status_response.json()["status"] == "cancelled_partial"
    assert status_response.json()["can_finalize"] is True
    assert finalize_response.status_code == 200
    assert finalize_response.json()["task_status"] == "cancelled_partial"


def test_agent_status_timeout_stops_with_partial_output(tmp_path, monkeypatch):
    async def fake_stop():
        calls.append("stop")
        return True

    class FakeProcess:
        def poll(self):
            return None

    calls = []
    data_root = tmp_path / "data"
    task_data_root = data_root / "agent_runs" / "agent_xhs_timeout"
    jsonl_dir = task_data_root / "xhs" / "jsonl"
    _write_jsonl(jsonl_dir / "search_contents_2026-07-08.jsonl", [{"note_id": "n1", "title": "AI"}])
    monkeypatch.setattr(agent_router, "DATA_DIR", data_root)
    monkeypatch.setattr(data_router, "DATA_DIR", data_root)
    monkeypatch.setattr(agent_router.crawler_manager, "process", FakeProcess())
    monkeypatch.setattr(agent_router.crawler_manager, "status", "running")
    monkeypatch.setattr(agent_router.crawler_manager, "last_exit_code", None)
    monkeypatch.setattr(agent_router.crawler_manager, "stop", fake_stop)
    agent_router._tasks.clear()
    agent_router._current_task_id = "agent_xhs_timeout"
    agent_router._tasks["agent_xhs_timeout"] = {
        "task_id": "agent_xhs_timeout",
        "status": "running",
        "keywords": ["AI"],
        "request": {"keywords": ["AI"], "max_contents": 20, "include_comments": False, "timeout_seconds": 30},
        "dataset_name": "",
        "description": "",
        "data_root": str(task_data_root),
        "started_at": datetime.now() - timedelta(seconds=31),
        "completed_at": None,
        "exit_code": None,
        "message": "running",
    }
    client = TestClient(app)

    response = client.get("/api/agent/tasks/agent_xhs_timeout")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "partial_success"
    assert payload["stop_reason"] == "timeout"
    assert payload["can_finalize"] is True
    assert calls == ["stop"]


def test_agent_status_captcha_backoff_stops_with_partial_output(tmp_path, monkeypatch):
    async def fake_stop():
        calls.append("stop")
        return True

    class FakeProcess:
        def poll(self):
            return None

    calls = []
    data_root = tmp_path / "data"
    task_data_root = data_root / "agent_runs" / "agent_xhs_captcha"
    jsonl_dir = task_data_root / "xhs" / "jsonl"
    _write_jsonl(jsonl_dir / "search_contents_2026-07-08.jsonl", [{"note_id": "n1", "title": "AI"}])
    monkeypatch.setattr(agent_router, "DATA_DIR", data_root)
    monkeypatch.setattr(data_router, "DATA_DIR", data_root)
    monkeypatch.setattr(agent_router.crawler_manager, "process", FakeProcess())
    monkeypatch.setattr(agent_router.crawler_manager, "status", "running")
    monkeypatch.setattr(agent_router.crawler_manager, "last_exit_code", None)
    monkeypatch.setattr(agent_router.crawler_manager, "stop", fake_stop)
    monkeypatch.setattr(
        agent_router,
        "_recent_logs",
        lambda limit=30: [{"level": "warning", "message": "detail api failed with 461 captcha"}] * 5,
    )
    agent_router._tasks.clear()
    agent_router._current_task_id = "agent_xhs_captcha"
    agent_router._tasks["agent_xhs_captcha"] = {
        "task_id": "agent_xhs_captcha",
        "status": "running",
        "keywords": ["AI"],
        "request": {"keywords": ["AI"], "max_contents": 20, "include_comments": False, "timeout_seconds": 1800},
        "dataset_name": "",
        "description": "",
        "data_root": str(task_data_root),
        "started_at": datetime.now(),
        "completed_at": None,
        "exit_code": None,
        "message": "running",
    }
    client = TestClient(app)

    response = client.get("/api/agent/tasks/agent_xhs_captcha")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "partial_success"
    assert payload["stop_reason"] == "captcha_backoff"
    assert calls == ["stop"]


def test_agent_task_cancel_and_retry(tmp_path, monkeypatch):
    async def fake_start(request):
        calls.append(("start", request.save_data_path))
        return True

    async def fake_stop():
        calls.append(("stop", None))
        return True

    class FakeProcess:
        def poll(self):
            return None

    calls = []
    data_root = tmp_path / "data"
    monkeypatch.setattr(agent_router, "DATA_DIR", data_root)
    monkeypatch.setattr(agent_router.crawler_manager, "process", FakeProcess())
    monkeypatch.setattr(agent_router.crawler_manager, "status", "running")
    monkeypatch.setattr(agent_router.crawler_manager, "last_exit_code", None)
    monkeypatch.setattr(agent_router.crawler_manager, "start", fake_start)
    monkeypatch.setattr(agent_router.crawler_manager, "stop", fake_stop)
    agent_router._tasks.clear()
    agent_router._current_task_id = "agent_xhs_cancel"
    agent_router._tasks["agent_xhs_cancel"] = {
        "task_id": "agent_xhs_cancel",
        "status": "running",
        "keywords": ["AI"],
        "request": {"keywords": ["AI"], "max_contents": 1},
        "dataset_name": "",
        "description": "",
        "data_root": str(data_root / "agent_runs" / "agent_xhs_cancel"),
        "started_at": datetime.now(),
        "completed_at": None,
        "exit_code": None,
        "message": "running",
    }
    client = TestClient(app)

    cancel_response = client.post("/api/agent/tasks/agent_xhs_cancel/cancel")
    monkeypatch.setattr(agent_router.crawler_manager, "process", None)
    retry_response = client.post("/api/agent/tasks/agent_xhs_cancel/retry")

    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"
    assert retry_response.status_code == 200
    assert retry_response.json()["retried_from"] == "agent_xhs_cancel"
    assert retry_response.json()["append"] is True
    assert retry_response.json()["task_id"].startswith("agent_xhs_")
    assert calls[0] == ("stop", None)
    assert calls[1][0] == "start"
    assert calls[1][1] == str(data_root / "agent_runs" / "agent_xhs_cancel")
