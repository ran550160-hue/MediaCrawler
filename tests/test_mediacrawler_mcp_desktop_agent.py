from mediacrawler_mcp.desktop_agent_client import convert_windows_paths, windows_path_to_wsl
from mediacrawler_mcp import server


def test_windows_path_to_wsl_default_drive_mapping():
    assert (
        windows_path_to_wsl(r"D:\WorkSpace\MediaCrawler\datasets\agent_bundle")
        == "/mnt/d/WorkSpace/MediaCrawler/datasets/agent_bundle"
    )


def test_windows_path_to_wsl_supports_root_override(monkeypatch):
    monkeypatch.setenv("MEDIACRAWLER_DESKTOP_WINDOWS_ROOT", r"D:\WorkSpace\MediaCrawler")
    monkeypatch.setenv("MEDIACRAWLER_DESKTOP_WSL_ROOT", "/workspace/MediaCrawler")

    assert (
        windows_path_to_wsl(r"D:\WorkSpace\MediaCrawler\datasets\agent_bundle")
        == "/workspace/MediaCrawler/datasets/agent_bundle"
    )


def test_convert_windows_paths_recurses_nested_payloads():
    payload = {
        "dataset_dir": r"D:\WorkSpace\MediaCrawler\datasets\agent_bundle",
        "data_root": r"D:\WorkSpace\MediaCrawler\data\agent_runs\agent_xhs_1",
        "files": [{"path": r"D:\WorkSpace\MediaCrawler\data\xhs\jsonl\a.jsonl"}],
        "download_url": "/api/data/download/xhs/jsonl/a.jsonl",
    }

    converted = convert_windows_paths(payload)

    assert converted["dataset_dir"] == "/mnt/d/WorkSpace/MediaCrawler/datasets/agent_bundle"
    assert converted["data_root"] == "/mnt/d/WorkSpace/MediaCrawler/data/agent_runs/agent_xhs_1"
    assert converted["files"][0]["path"] == "/mnt/d/WorkSpace/MediaCrawler/data/xhs/jsonl/a.jsonl"
    assert converted["download_url"] == "/api/data/download/xhs/jsonl/a.jsonl"


def test_desktop_agent_tools_delegate_to_local_workbench(monkeypatch):
    calls = []

    class FakeDesktopAgentClient:
        def __init__(self, base_url=None):
            self.base_url = base_url or "http://127.0.0.1:8080"

        def start_xhs_search(self, **kwargs):
            calls.append(("start", kwargs))
            return {"task_id": "agent_xhs_1", "status": "running"}

        def get_task_status(self, task_id, include_logs=False, include_files=True, log_limit=10):
            calls.append(("status", task_id, include_logs, include_files, log_limit))
            return {"task_id": task_id, "status": "completed"}

    monkeypatch.setattr(server, "DesktopAgentClient", FakeDesktopAgentClient)

    started = server.start_local_xhs_search(["AI"], max_contents=5)
    status = server.get_local_xhs_search_status("agent_xhs_1")

    assert started == {"task_id": "agent_xhs_1", "status": "running"}
    assert status == {"task_id": "agent_xhs_1", "status": "completed"}
    assert calls[0][0] == "start"
    assert calls[0][1]["keywords"] == ["AI"]
    assert calls[0][1]["max_contents"] == 5
    assert calls[1] == ("status", "agent_xhs_1", False, True, 10)


def test_desktop_agent_cancel_and_retry_delegate_to_local_workbench(monkeypatch):
    calls = []

    class FakeDesktopAgentClient:
        def __init__(self, base_url=None):
            pass

        def cancel_task(self, task_id):
            calls.append(("cancel", task_id))
            return {"task_id": task_id, "status": "cancelled"}

        def retry_task(self, task_id):
            calls.append(("retry", task_id))
            return {"retried_from": task_id, "task_id": "agent_xhs_2", "status": "running"}

    monkeypatch.setattr(server, "DesktopAgentClient", FakeDesktopAgentClient)

    cancelled = server.cancel_local_xhs_search("agent_xhs_1")
    retried = server.retry_local_xhs_search("agent_xhs_1")

    assert cancelled["status"] == "cancelled"
    assert retried["task_id"] == "agent_xhs_2"
    assert calls == [("cancel", "agent_xhs_1"), ("retry", "agent_xhs_1")]


def test_finalize_local_xhs_search_registers_normalizes_and_reports(monkeypatch):
    class FakeDesktopAgentClient:
        def __init__(self, base_url=None):
            pass

        def finalize_task(self, task_id, dataset_name="", description=""):
            return {
                "dataset_dir": "/mnt/d/WorkSpace/MediaCrawler/datasets/agent_bundle",
                "dataset_id": "agent_bundle",
                "files": [{"path": "/mnt/d/WorkSpace/MediaCrawler/data/xhs/jsonl/a.jsonl"}],
            }

    class FakeImporter:
        def register_dataset(self, dataset_dir, import_mode="copy"):
            assert dataset_dir == "/mnt/d/WorkSpace/MediaCrawler/datasets/agent_bundle"
            assert import_mode == "link"
            return {"dataset_id": "agent_bundle", "dataset_dir": dataset_dir}

    class FakeNormalizer:
        def __init__(self, storage):
            pass

        def normalize_dataset(self, dataset_id, force=False):
            assert dataset_id == "agent_bundle"
            assert force is True
            return {"content_count": 1, "comment_count": 1}

    class FakeReportService:
        def __init__(self, storage):
            pass

        def generate_report(self, dataset_id):
            assert dataset_id == "agent_bundle"
            return {"report_md_path": "/tmp/report.md"}

    monkeypatch.setattr(server, "DesktopAgentClient", FakeDesktopAgentClient)
    monkeypatch.setattr(server, "_importer", lambda: FakeImporter())
    monkeypatch.setattr(server, "_storage", lambda: object())
    monkeypatch.setattr(server, "DatasetNormalizer", FakeNormalizer)
    monkeypatch.setattr(server, "ReportService", FakeReportService)

    result = server.finalize_local_xhs_search("agent_xhs_1")

    assert result["status"] == "success"
    assert result["dataset_id"] == "agent_bundle"
    assert result["registered"]["dataset_id"] == "agent_bundle"
    assert result["normalized"] == {"content_count": 1, "comment_count": 1}
    assert result["report"] == {"report_md_path": "/tmp/report.md"}
    assert result["preview"] == [{"path": "/mnt/d/WorkSpace/MediaCrawler/data/xhs/jsonl/a.jsonl"}]
