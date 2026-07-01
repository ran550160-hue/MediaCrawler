from __future__ import annotations

from pathlib import Path

from scripts.hermes_mcp_smoke import run_smoke


def test_hermes_mcp_smoke_workflow(tmp_path):
    result = run_smoke(tmp_path / "mcp_home")

    assert result["status"] == "success"
    assert result["counts"]["datasets_found"] == 1
    assert result["counts"]["contents"] == 2
    assert result["counts"]["comments"] == 2
    assert result["counts"]["query_results"] == 1
    assert "mcp_mediacrawler_query_dataset" in result["tool_name_hint"]
    for output_path in result["outputs"].values():
        assert Path(output_path).exists()
