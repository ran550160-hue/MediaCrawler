from __future__ import annotations

import asyncio
import inspect
import importlib

from mediacrawler_mcp import server


EXPERIMENTAL_TOOL_NAMES = {
    "get_login_status",
    "import_cookies",
    "start_qrcode_login",
    "get_qrcode_login_status",
    "cancel_qrcode_login",
    "start_collection",
    "get_task_status",
    "cancel_task",
}

DESKTOP_AGENT_TOOL_NAMES = {
    "check_local_workbench",
    "ensure_cdp_browser",
    "start_local_xhs_search",
    "get_local_xhs_search_status",
    "cancel_local_xhs_search",
    "retry_local_xhs_search",
    "finalize_local_xhs_search",
}


def _tool_names(server_module=server) -> set[str]:
    return {tool.name for tool in asyncio.run(server_module.mcp.list_tools())}


def test_default_dataset_profile_hides_experimental_collection_tools(monkeypatch):
    monkeypatch.setenv("MEDIACRAWLER_MCP_TOOL_PROFILE", "dataset")
    monkeypatch.delenv("MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION", raising=False)
    server_module = importlib.reload(server)

    names = _tool_names(server_module)

    assert {
        "create_dataset",
        "register_dataset",
        "validate_dataset_bundle",
        "import_raw_files",
        "sync_dataset_manifest",
        "list_datasets",
        "get_dataset",
        "normalize_dataset",
        "query_dataset",
        "generate_report",
        "generate_topic_research_report",
        "get_report",
    }.issubset(names)
    assert EXPERIMENTAL_TOOL_NAMES.isdisjoint(names)
    assert DESKTOP_AGENT_TOOL_NAMES.isdisjoint(names)


def test_desktop_agent_profile_exposes_local_tools_without_experimental_tools(monkeypatch):
    monkeypatch.setenv("MEDIACRAWLER_MCP_TOOL_PROFILE", "desktop_agent")
    monkeypatch.delenv("MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION", raising=False)
    server_module = importlib.reload(server)

    names = _tool_names(server_module)

    assert DESKTOP_AGENT_TOOL_NAMES.issubset(names)
    assert EXPERIMENTAL_TOOL_NAMES.isdisjoint(names)

    monkeypatch.setenv("MEDIACRAWLER_MCP_TOOL_PROFILE", "dataset")
    importlib.reload(server)


def test_experimental_collection_tools_require_explicit_enable(monkeypatch):
    monkeypatch.setenv("MEDIACRAWLER_MCP_TOOL_PROFILE", "dataset")
    monkeypatch.setenv("MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION", "true")
    server_module = importlib.reload(server)

    assert EXPERIMENTAL_TOOL_NAMES.issubset(_tool_names(server_module))

    monkeypatch.setenv("MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION", "false")
    importlib.reload(server)


def test_start_collection_schema_defaults_to_remote_verify():
    signature = inspect.signature(server.start_collection)

    assert signature.parameters["verify_login_remote"].default is True
    assert signature.parameters["skip_preflight"].default is False


def test_report_entry_schemas_default_to_topic_research_top_n_ten():
    assert inspect.signature(server.generate_report).parameters["top_n"].default == 10
    assert inspect.signature(server.generate_topic_research_report).parameters["top_n"].default == 10


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


def test_generate_report_dispatches_each_report_type_without_ambiguous_topic_alias(monkeypatch):
    calls = []

    class FakeTopicResearchService:
        def __init__(self, storage):
            pass

        def generate_topic_research_report(self, dataset_id, top_n=10):
            calls.append(("topic", dataset_id, top_n))
            return {"report_type": "topic_research"}

    class FakeReportService:
        def __init__(self, storage):
            pass

        def generate_report(self, dataset_id, report_type="generic", top_n=20):
            calls.append(("generic", dataset_id, report_type, top_n))
            return {"report_type": report_type}

    monkeypatch.setattr(server, "TopicResearchService", FakeTopicResearchService)
    monkeypatch.setattr(server, "ReportService", FakeReportService)
    monkeypatch.setattr(server, "_storage", lambda: object())

    assert server.generate_report("dataset", report_type="topic_research", top_n=7)["report_type"] == "topic_research"
    assert server.generate_report("dataset", report_type="generic", top_n=7)["report_type"] == "generic"
    assert server.generate_report("dataset", report_type="none")["report"] is None
    assert calls == [("topic", "dataset", 7), ("generic", "dataset", "generic", 7)]
