from __future__ import annotations

from pathlib import Path

from scripts.hermes_mcp_smoke import run_smoke, write_fixture_raw_files


def test_hermes_mcp_smoke_fixture_bundle_workflow(tmp_path):
    result = run_smoke(tmp_path / "mcp_home")

    assert result["status"] == "success"
    assert result["mode"] == "fixture_bundle"
    assert result["profile"]["experimental_tools_hidden"] is True
    assert result["counts"]["datasets_found"] == 1
    assert result["counts"]["contents"] == 2
    assert result["counts"]["comments"] == 3
    assert result["counts"]["query_results"] == 3
    assert "mcp_mediacrawler_register_dataset" in result["tool_name_hint"]
    assert "mcp_mediacrawler_query_dataset" in result["tool_name_hint"]
    assert [step["tool"] for step in result["steps"]] == [
        "mcp.list_tools",
        "ping",
        "validate_dataset_bundle",
        "register_dataset",
        "list_datasets",
        "get_dataset",
        "normalize_dataset",
        "query_dataset",
        "generate_report",
        "get_report",
    ]
    for output_path in result["outputs"].values():
        assert Path(output_path).exists()


def test_hermes_mcp_smoke_raw_file_import_workflow(tmp_path):
    raw_files = write_fixture_raw_files(tmp_path / "desktop_raw")

    result = run_smoke(
        tmp_path / "mcp_home",
        contents_path=raw_files["contents"],
        comments_path=raw_files["comments"],
    )

    assert result["status"] == "success"
    assert result["mode"] == "raw_files"
    assert result["counts"]["datasets_found"] == 1
    assert result["counts"]["contents"] == 2
    assert result["counts"]["comments"] == 3
    assert result["counts"]["query_results"] == 3
    assert "contents" in result["import"]["imported_raw_files"]
    assert "comments" in result["import"]["imported_raw_files"]
    assert "import_raw_files" in [step["tool"] for step in result["steps"]]
    assert "sync_dataset_manifest" in [step["tool"] for step in result["steps"]]
    for output_path in result["outputs"].values():
        assert Path(output_path).exists()
