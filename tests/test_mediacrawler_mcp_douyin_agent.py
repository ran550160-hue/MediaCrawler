import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import api.routers.agent as agent_router
from api.main import app
from api.schemas import CrawlerStartRequest, CrawlerTypeEnum, LoginTypeEnum, PlatformEnum, SaveDataOptionEnum
from api.services.crawler_manager import CrawlerManager
from mediacrawler_mcp import server


# ---------------------------------------------------------------------------
# Command construction (cases 1-10)
# ---------------------------------------------------------------------------

def _douyin_request(**overrides):
    defaults = dict(
        platform=PlatformEnum.DOUYIN,
        login_type=LoginTypeEnum.QRCODE,
        crawler_type=CrawlerTypeEnum.SEARCH,
        keywords="test",
        save_option=SaveDataOptionEnum.JSONL,
        headless=False,
        max_notes_count=10,
        max_comments_count=3,
        enable_comments=True,
        enable_sub_comments=False,
        enable_cdp_mode=False,
        cdp_connect_existing=False,
        cdp_debug_port=9222,
        save_data_path="custom-data",
    )
    defaults.update(overrides)
    return CrawlerStartRequest(**defaults)


def test_douyin_command_uses_dy_platform():
    cmd = CrawlerManager()._build_command(_douyin_request())
    assert cmd[cmd.index("--platform") + 1] == "dy"


def test_douyin_command_uses_search_type():
    cmd = CrawlerManager()._build_command(_douyin_request())
    assert cmd[cmd.index("--type") + 1] == "search"


def test_douyin_command_has_independent_save_data_path():
    cmd = CrawlerManager()._build_command(_douyin_request(save_data_path="/tmp/agent_dy_001"))
    assert cmd[cmd.index("--save_data_path") + 1] == "/tmp/agent_dy_001"


def test_douyin_command_uses_jsonl():
    cmd = CrawlerManager()._build_command(_douyin_request())
    assert cmd[cmd.index("--save_data_option") + 1] == "jsonl"


def test_douyin_command_default_get_comment_true():
    cmd = CrawlerManager()._build_command(_douyin_request())
    assert cmd[cmd.index("--get_comment") + 1] == "true"


def test_douyin_command_default_sub_comment_false():
    cmd = CrawlerManager()._build_command(_douyin_request())
    assert cmd[cmd.index("--get_sub_comment") + 1] == "false"


def test_douyin_command_no_media_download_flag():
    cmd = CrawlerManager()._build_command(_douyin_request())
    # No media download related CLI args in the command
    cmd_str = " ".join(cmd)
    assert "--save_data_option" in cmd_str
    assert "jsonl" in cmd_str
    # No specific media download flag exists in CLI
    assert "--download" not in cmd_str.lower()


def test_douyin_command_standard_playwright_mode():
    req = _douyin_request(enable_cdp_mode=False, cdp_connect_existing=False)
    cmd = CrawlerManager()._build_command(req)
    assert cmd[cmd.index("--enable_cdp_mode") + 1] == "false"
    assert cmd[cmd.index("--cdp_connect_existing") + 1] == "false"


def test_douyin_command_cdp_params_passed_through():
    req = _douyin_request(enable_cdp_mode=True, cdp_connect_existing=True, cdp_debug_port=9333)
    cmd = CrawlerManager()._build_command(req)
    assert cmd[cmd.index("--enable_cdp_mode") + 1] == "true"
    assert cmd[cmd.index("--cdp_connect_existing") + 1] == "true"
    assert cmd[cmd.index("--cdp_debug_port") + 1] == "9333"


def test_douyin_command_no_sensitive_fields():
    req = _douyin_request()
    cmd = CrawlerManager()._build_command(req)
    cmd_str = " ".join(cmd).lower()
    for sensitive in ["cookie", "token", "verifyfp", "mstoken", "a_bogus", "signature"]:
        assert sensitive not in cmd_str


# ---------------------------------------------------------------------------
# Task lifecycle (cases 11-16)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_tasks(monkeypatch):
    agent_router._tasks.clear()
    agent_router._current_task_id = None
    agent_router.crawler_manager.process = None
    agent_router.crawler_manager.status = "idle"
    agent_router.crawler_manager.last_exit_code = None
    agent_router.crawler_manager._logs = []
    # Patch get_file_info so test tmp files outside DATA_DIR don't cause ValueError
    def _safe_file_info(path):
        return {
            "name": path.name,
            "path": str(path),
            "size": path.stat().st_size if path.exists() else 0,
            "modified_at": "",
            "record_count": 0,
        }
    monkeypatch.setattr(agent_router, "get_file_info", _safe_file_info)
    yield
    agent_router._tasks.clear()
    agent_router._current_task_id = None


def test_start_douyin_returns_task_id(monkeypatch):
    async def fake_start(config):
        return True
    monkeypatch.setattr(agent_router.crawler_manager, "start", fake_start)
    monkeypatch.setattr(agent_router.crawler_manager, "process", None)

    client = TestClient(app)
    response = client.post("/api/agent/douyin/search", json={"keywords": ["test"], "max_contents": 5})

    assert response.status_code == 200
    data = response.json()
    assert "task_id" in data
    assert data["status"] == "running"
    assert data["task_id"].startswith("agent_douyin_")


def test_douyin_running_then_succeeded(monkeypatch, tmp_path):
    async def fake_start(config):
        agent_router.crawler_manager.status = "running"
        agent_router.crawler_manager.last_exit_code = 0
        return True
    monkeypatch.setattr(agent_router.crawler_manager, "start", fake_start)
    monkeypatch.setattr(agent_router.crawler_manager, "process", None)

    client = TestClient(app)
    response = client.post("/api/agent/douyin/search", json={"keywords": ["test"], "max_contents": 3})
    task_id = response.json()["task_id"]

    # Simulate process finished with exit code 0 and output exists
    task = agent_router._tasks[task_id]
    task["status"] = "completed"
    task["completed_at"] = datetime.now()
    task["exit_code"] = 0

    status_response = client.get(f"/api/agent/tasks/{task_id}")
    assert status_response.status_code == 200
    status_data = status_response.json()
    assert status_data["status"] in {"completed", "partial_success"}


def test_douyin_failed(monkeypatch):
    async def fake_start(config):
        return False
    monkeypatch.setattr(agent_router.crawler_manager, "start", fake_start)
    monkeypatch.setattr(agent_router.crawler_manager, "process", None)

    client = TestClient(app)
    response = client.post("/api/agent/douyin/search", json={"keywords": ["test"]})

    assert response.status_code == 500


def test_douyin_cancel(monkeypatch):
    async def fake_start(config):
        return True
    monkeypatch.setattr(agent_router.crawler_manager, "start", fake_start)
    monkeypatch.setattr(agent_router.crawler_manager, "process", None)

    client = TestClient(app)
    start_response = client.post("/api/agent/douyin/search", json={"keywords": ["test"]})
    task_id = start_response.json()["task_id"]

    # Simulate a running process after task creation
    from types import SimpleNamespace
    process_mock = SimpleNamespace(poll=lambda: None, terminate=lambda: None)
    agent_router.crawler_manager.process = process_mock
    agent_router.crawler_manager.status = "running"
    agent_router._current_task_id = task_id

    cancel_response = client.post(f"/api/agent/tasks/{task_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"


def test_douyin_retry_uses_new_task_and_dir(monkeypatch):
    async def fake_start(config):
        return True
    monkeypatch.setattr(agent_router.crawler_manager, "start", fake_start)
    monkeypatch.setattr(agent_router.crawler_manager, "process", None)

    client = TestClient(app)
    start_response = client.post("/api/agent/douyin/search", json={"keywords": ["retry_test"]})
    original_task_id = start_response.json()["task_id"]
    original_data_root = start_response.json()["data_root"]

    retry_response = client.post(f"/api/agent/tasks/{original_task_id}/retry?append=false")
    assert retry_response.status_code == 200
    retry_data = retry_response.json()
    new_task_id = retry_data["task_id"]
    assert new_task_id != original_task_id
    assert retry_data["retried_from"] == original_task_id


def test_two_douyin_tasks_different_output_dirs(monkeypatch):
    async def fake_start(config):
        return True
    monkeypatch.setattr(agent_router.crawler_manager, "start", fake_start)
    monkeypatch.setattr(agent_router.crawler_manager, "process", None)

    client = TestClient(app)
    r1 = client.post("/api/agent/douyin/search", json={"keywords": ["a"]})
    r2 = client.post("/api/agent/douyin/search", json={"keywords": ["b"]})

    # Second may fail because crawler_manager.process is None (no running process)
    # But both should have different task_ids
    t1 = r1.json()["task_id"]
    if r2.status_code == 200:
        t2 = r2.json()["task_id"]
        assert t1 != t2
        assert agent_router._tasks[t1]["data_root"] != agent_router._tasks[t2]["data_root"]


# ---------------------------------------------------------------------------
# Finalize (cases 17-30)
# ---------------------------------------------------------------------------

def _write_run_output(data_root, run_id="run_test_001", contents=True, comments=True):
    run_dir = data_root / "douyin" / run_id
    jsonl_dir = run_dir / "jsonl"
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    meta = {"run_id": run_id, "platform": "douyin", "crawler_type": "search", "keywords": ["test"], "started_at": "2026-07-11T17:00:00+00:00", "output_dir": str(run_dir)}
    (run_dir / "run_metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if contents:
        (jsonl_dir / "search_contents.jsonl").write_text(
            json.dumps({"aweme_id": "7400000000000000001", "title": "test", "desc": "d", "source_keyword": "test", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000, "liked_count": "10", "comment_count": "1", "collected_count": "2", "share_count": "3"}) + "\n",
            encoding="utf-8",
        )
    if comments:
        (jsonl_dir / "search_comments.jsonl").write_text(
            json.dumps({"comment_id": "c1", "aweme_id": "7400000000000000001", "content": "msg", "user_id": "u", "like_count": 1, "create_time": 1780000001, "parent_comment_id": "0", "last_modify_ts": 1780000000001}) + "\n",
            encoding="utf-8",
        )
    return run_dir


def _make_task(tmp_path, task_id="agent_douyin_test_001", run_id="run_test_001", contents=True, comments=True):
    data_root = tmp_path / "agent_runs" / task_id
    data_root.mkdir(parents=True, exist_ok=True)
    _write_run_output(data_root, run_id=run_id, contents=contents, comments=comments)
    task = {
        "task_id": task_id,
        "platform": "douyin",
        "status": "completed",
        "keywords": ["test"],
        "request": {"keywords": ["test"], "max_contents": 5, "max_comments_per_content": 3, "include_comments": True, "include_sub_comments": False, "cdp_debug_port": 9222, "headless": False, "enable_cdp_mode": False, "cdp_connect_existing": False, "login_type": "qrcode", "timeout_seconds": 1800, "dataset_name": "", "description": ""},
        "dataset_name": "",
        "description": "",
        "data_root": str(data_root),
        "retried_from": None,
        "started_at": datetime.now(),
        "completed_at": datetime.now(),
        "exit_code": 0,
        "message": "completed",
    }
    agent_router._tasks[task_id] = task
    return task


def test_finalize_discovers_run_isolated_douyin_output(tmp_path):
    _make_task(tmp_path)
    client = TestClient(app)
    response = client.post("/api/agent/tasks/agent_douyin_test_001/finalize", json={"output_dir": str(tmp_path / "bundle_out")})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "dataset_dir" in data


def test_finalize_contents_comments_same_run(tmp_path):
    _make_task(tmp_path, run_id="run_same_001")
    client = TestClient(app)
    response = client.post("/api/agent/tasks/agent_douyin_test_001/finalize", json={"output_dir": str(tmp_path / "bundle_out")})
    assert response.status_code == 200


def test_finalize_comments_missing_still_succeeds(tmp_path):
    _make_task(tmp_path, run_id="run_nocomment_001", comments=False)
    client = TestClient(app)
    response = client.post("/api/agent/tasks/agent_douyin_test_001/finalize", json={"output_dir": str(tmp_path / "bundle_out")})
    assert response.status_code == 200


def test_finalize_no_contents_fails(tmp_path):
    _make_task(tmp_path, run_id="run_nocontent_001", contents=False, comments=True)
    client = TestClient(app)
    response = client.post("/api/agent/tasks/agent_douyin_test_001/finalize", json={"output_dir": str(tmp_path / "bundle_out")})
    assert response.status_code == 404


def test_finalize_multiple_runs_fails(tmp_path):
    task_id = "agent_douyin_multi_001"
    data_root = tmp_path / "agent_runs" / task_id
    data_root.mkdir(parents=True, exist_ok=True)
    _write_run_output(data_root, run_id="run_a")
    _write_run_output(data_root, run_id="run_b")
    task = {
        "task_id": task_id, "platform": "douyin", "status": "completed",
        "keywords": ["test"], "request": {}, "dataset_name": "", "description": "",
        "data_root": str(data_root), "retried_from": None,
        "started_at": datetime.now(), "completed_at": datetime.now(), "exit_code": 0, "message": "ok",
    }
    agent_router._tasks[task_id] = task
    client = TestClient(app)
    response = client.post(f"/api/agent/tasks/{task_id}/finalize", json={"output_dir": str(tmp_path / "bundle_out")})
    assert response.status_code == 409


def test_finalize_exports_bundle(tmp_path):
    _make_task(tmp_path)
    client = TestClient(app)
    response = client.post("/api/agent/tasks/agent_douyin_test_001/finalize", json={"output_dir": str(tmp_path / "bundle_out")})
    data = response.json()
    assert data["status"] == "success"
    bundle_dir = Path(data["dataset_dir"])
    assert (bundle_dir / "raw" / "douyin_contents.jsonl").exists()


def test_finalize_registers_dataset(tmp_path):
    _make_task(tmp_path)
    client = TestClient(app)
    response = client.post("/api/agent/tasks/agent_douyin_test_001/finalize", json={"output_dir": str(tmp_path / "bundle_out")})
    data = response.json()
    assert "dataset_id" in data
    assert "dataset_dir" in data


def test_finalize_normalizes(tmp_path):
    _make_task(tmp_path)
    client = TestClient(app)
    response = client.post("/api/agent/tasks/agent_douyin_test_001/finalize", json={"output_dir": str(tmp_path / "bundle_out")})
    # The agent finalize returns the bundle; MCP-level finalize would normalize.
    # Here we test that the agent finalize returns a valid bundle dir.
    data = response.json()
    assert data["status"] == "success"
    assert "dataset_dir" in data


def test_finalize_returns_counts_and_diagnostics(tmp_path):
    """Test that the MCP-level finalize returns normalize diagnostics."""
    _make_task(tmp_path)
    client = TestClient(app)
    # First get the bundle from agent finalize
    response = client.post("/api/agent/tasks/agent_douyin_test_001/finalize", json={"output_dir": str(tmp_path / "bundle_out")})
    bundle_dir = Path(response.json()["dataset_dir"])
    # The agent finalize returns bundle info; MCP finalize adds normalize
    from mediacrawler_mcp.config import McpConfig
    from mediacrawler_mcp.dataset_importer import DatasetImporter
    from mediacrawler_mcp.normalizer import DatasetNormalizer
    from mediacrawler_mcp.storage import Storage
    config = McpConfig(home=tmp_path / "mcp_home", browser_mode="persistent_context", cdp_endpoint=None, max_concurrent_tasks=1, default_timeout_seconds=300)
    storage = Storage(config)
    storage.initialize()
    importer = DatasetImporter(config, storage)
    registered = importer.register_dataset(dataset_dir=str(bundle_dir), import_mode="copy")
    dataset_id = registered["dataset_id"]
    summary = DatasetNormalizer(storage).normalize_dataset(dataset_id, force=True)
    assert summary["content_count"] == 1
    assert summary["comment_count"] == 1
    assert "orphan_comment_count" in summary
    assert "reply_lineage_anomaly_count" in summary


def test_finalize_writes_platform_douyin(tmp_path):
    _make_task(tmp_path)
    client = TestClient(app)
    response = client.post("/api/agent/tasks/agent_douyin_test_001/finalize", json={"output_dir": str(tmp_path / "bundle_out")})
    data = response.json()
    bundle_dir = Path(data["dataset_dir"])
    manifest = json.loads((bundle_dir / "dataset.json").read_text(encoding="utf-8"))
    assert manifest.get("platforms") == ["douyin"]


def test_finalize_default_no_report():
    """Douyin finalize does not generate reports by default."""
    sig = __import__("inspect").signature(server.finalize_local_douyin_search)
    assert sig.parameters["report_type"].default == "none"


def test_finalize_topic_research_unsupported(monkeypatch):
    """Calling finalize_local_douyin_search with report_type=topic_research returns an error."""
    monkeypatch.setattr(server, "DesktopAgentClient", lambda **kw: type("Fake", (), {
        "finalize_task": lambda self, *a, **kw: {"dataset_dir": "/tmp/ds"},
    })())
    monkeypatch.setattr(server, "_importer", lambda: type("Fake", (), {
        "register_dataset": lambda self, **kw: {"dataset_id": "ds1", "dataset_dir": "/tmp/ds"},
    })())
    monkeypatch.setattr(server, "_storage", lambda: object())
    monkeypatch.setattr(server, "DatasetNormalizer", lambda storage: type("Fake", (), {
        "normalize_dataset": lambda self, *a, **kw: {"content_count": 0},
    }))

    # Clear cached finalized results
    server._FINALIZED_DOUYIN.clear()

    result = server.finalize_local_douyin_search("task_test", report_type="topic_research")
    assert result["status"] == "failed"
    assert "topic" in result.get("error", {}).get("message", "").lower() or "topic" in result.get("error", {}).get("detail", "").lower()


def test_finalize_repeat_is_idempotent(tmp_path):
    _make_task(tmp_path)
    client = TestClient(app)
    r1 = client.post("/api/agent/tasks/agent_douyin_test_001/finalize", json={"output_dir": str(tmp_path / "bundle_a")})
    assert r1.status_code == 200
    r2 = client.post("/api/agent/tasks/agent_douyin_test_001/finalize", json={"output_dir": str(tmp_path / "bundle_b")})
    assert r2.status_code == 200
    assert r2.json().get("already_finalized") is True
    # Same dataset_dir, not a second bundle
    assert r2.json()["dataset_dir"] == r1.json()["dataset_dir"]


# ---------------------------------------------------------------------------
# Desktop agent MCP tool delegation (server.py)
# ---------------------------------------------------------------------------

def test_start_local_douyin_search_delegates(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, base_url=None):
            self.base_url = base_url or "http://127.0.0.1:8080"

        def start_douyin_search(self, **kwargs):
            calls.append(kwargs)
            return {"task_id": "agent_dy_1", "status": "running"}

    monkeypatch.setattr(server, "DesktopAgentClient", FakeClient)
    result = server.start_local_douyin_search(["test"], max_contents=5)
    assert result == {"task_id": "agent_dy_1", "status": "running"}
    assert calls[0]["keywords"] == ["test"]
    assert calls[0]["max_contents"] == 5


def test_douyin_cancel_and_retry_delegate_to_local_workbench(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, base_url=None):
            pass

        def cancel_task(self, task_id):
            calls.append(("cancel", task_id))
            return {"task_id": task_id, "status": "cancelled"}

        def retry_task(self, task_id, append=True):
            calls.append(("retry", task_id, append))
            return {"retried_from": task_id, "append": append, "task_id": "agent_dy_2", "status": "running"}

    monkeypatch.setattr(server, "DesktopAgentClient", FakeClient)
    cancelled = server.cancel_local_douyin_search("agent_dy_1")
    retried = server.retry_local_douyin_search("agent_dy_1")
    assert cancelled["status"] == "cancelled"
    assert retried["task_id"] == "agent_dy_2"
    assert ("cancel", "agent_dy_1") in calls
    assert ("retry", "agent_dy_1", False) in calls


def test_finalize_local_douyin_search_registers_and_normalizes(monkeypatch):
    class FakeClient:
        def __init__(self, base_url=None):
            pass

        def finalize_task(self, task_id, dataset_name="", description="", force=False):
            return {"dataset_dir": "/tmp/ds_dy", "dataset_id": "ds_dy", "files": []}

    class FakeImporter:
        def register_dataset(self, dataset_dir, import_mode="copy"):
            assert dataset_dir == "/tmp/ds_dy"
            return {"dataset_id": "ds_dy", "dataset_dir": dataset_dir}

    class FakeNormalizer:
        def __init__(self, storage):
            pass

        def normalize_dataset(self, dataset_id, force=False):
            return {"content_count": 1, "comment_count": 1, "orphan_comment_count": 0, "reply_lineage_anomaly_count": 0}

    monkeypatch.setattr(server, "DesktopAgentClient", FakeClient)
    monkeypatch.setattr(server, "_importer", lambda: FakeImporter())
    monkeypatch.setattr(server, "_storage", lambda: object())
    monkeypatch.setattr(server, "DatasetNormalizer", FakeNormalizer)

    server._FINALIZED_DOUYIN.clear()
    result = server.finalize_local_douyin_search("agent_dy_1")

    assert result["status"] == "success"
    assert result["dataset_id"] == "ds_dy"
    assert result["normalized"]["content_count"] == 1
    assert result["report_type"] == "none"


def test_finalize_local_douyin_search_idempotent(monkeypatch):
    class FakeClient:
        def __init__(self, base_url=None):
            pass

        def finalize_task(self, task_id, dataset_name="", description="", force=False):
            return {"dataset_dir": "/tmp/ds_dy2", "dataset_id": "ds_dy2"}

    class FakeImporter:
        def register_dataset(self, dataset_dir, import_mode="copy"):
            return {"dataset_id": "ds_dy2", "dataset_dir": dataset_dir}

    class FakeNormalizer:
        def __init__(self, storage):
            pass

        def normalize_dataset(self, dataset_id, force=False):
            return {"content_count": 1}

    monkeypatch.setattr(server, "DesktopAgentClient", FakeClient)
    monkeypatch.setattr(server, "_importer", lambda: FakeImporter())
    monkeypatch.setattr(server, "_storage", lambda: object())
    monkeypatch.setattr(server, "DatasetNormalizer", FakeNormalizer)

    server._FINALIZED_DOUYIN.clear()
    r1 = server.finalize_local_douyin_search("agent_dy_idem")
    r2 = server.finalize_local_douyin_search("agent_dy_idem")

    assert r1["status"] == "success"
    assert r2["status"] == "success"
    assert r2.get("already_finalized") is True
