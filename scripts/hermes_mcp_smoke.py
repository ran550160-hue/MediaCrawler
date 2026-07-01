from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _assert_success(name: str, result: dict[str, Any]) -> None:
    if result.get("status") != "success":
        raise RuntimeError(f"{name} failed: {json.dumps(result, ensure_ascii=False)}")


def _seed_raw_dataset(dataset_dir: Path) -> None:
    raw_dir = dataset_dir / "raw"
    _write_jsonl(
        raw_dir / "xhs_contents.jsonl",
        [
            {
                "note_id": "smoke_note_1",
                "note_url": "https://www.xiaohongshu.com/explore/smoke_note_1",
                "title": "AI coding side hustle feedback",
                "desc": "Users compare course quality and real freelance risks.",
                "nickname": "creator-a",
                "liked_count": "120",
                "collected_count": "30",
                "comment_count": "2",
                "share_count": "5",
                "source_keyword": "AI coding side hustle",
            },
            {
                "note_id": "smoke_note_2",
                "note_url": "https://www.xiaohongshu.com/explore/smoke_note_2",
                "title": "Programmer freelance checklist",
                "desc": "Practical checklist for small paid projects.",
                "nickname": "creator-b",
                "liked_count": "80",
                "collected_count": "20",
                "comment_count": "1",
                "share_count": "3",
                "source_keyword": "programmer freelance",
            },
        ],
    )
    _write_jsonl(
        raw_dir / "xhs_comments.jsonl",
        [
            {
                "comment_id": "smoke_comment_1",
                "note_id": "smoke_note_1",
                "nickname": "user-a",
                "content": "Real feedback: pricing and client trust are the hardest parts.",
                "like_count": "12",
                "source_keyword": "AI coding side hustle",
            },
            {
                "comment_id": "smoke_comment_2",
                "note_id": "smoke_note_1",
                "nickname": "user-b",
                "content": "I care more about course quality than income screenshots.",
                "like_count": "8",
                "source_keyword": "AI coding side hustle",
            },
        ],
    )


def run_smoke(home: Path) -> dict[str, Any]:
    os.environ["MEDIACRAWLER_MCP_HOME"] = str(home)

    from mediacrawler_mcp.server import (  # noqa: PLC0415
        create_dataset,
        generate_report,
        get_dataset,
        get_report,
        list_datasets,
        normalize_dataset,
        ping,
        query_dataset,
    )

    ping_result = ping("hermes")
    if ping_result != "pong:hermes":
        raise RuntimeError(f"ping failed: {ping_result}")

    created = create_dataset(
        name="Hermes MCP smoke dataset",
        platforms=["xhs"],
        keywords=["AI coding side hustle", "programmer freelance"],
        description="Offline fixture used to verify Hermes MCP tool chaining.",
    )
    _assert_success("create_dataset", created)
    dataset_id = created["dataset_id"]
    dataset_dir = Path(created["dataset_dir"])
    _seed_raw_dataset(dataset_dir)

    listed = list_datasets(keyword="Hermes", platform="xhs")
    _assert_success("list_datasets", listed)
    fetched = get_dataset(dataset_id)
    _assert_success("get_dataset", fetched)
    normalized = normalize_dataset(dataset_id, force=True)
    _assert_success("normalize_dataset", normalized)
    queried = query_dataset(dataset_id, query="feedback", target="comments", limit=5)
    _assert_success("query_dataset", queried)
    reported = generate_report(dataset_id, report_type="topic_research", top_n=5)
    _assert_success("generate_report", reported)
    latest_report = get_report(dataset_id)
    _assert_success("get_report", latest_report)

    paths_to_check = [
        Path(normalized["duckdb_path"]),
        Path(reported["report_md_path"]),
        Path(reported["report_html_path"]),
        Path(reported["summary_json_path"]),
    ]
    missing = [str(path) for path in paths_to_check if not path.exists()]
    if missing:
        raise RuntimeError(f"expected output paths missing: {missing}")

    return {
        "status": "success",
        "home": str(home),
        "dataset_id": dataset_id,
        "dataset_dir": str(dataset_dir),
        "tool_name_hint": [
            "mcp_mediacrawler_create_dataset",
            "mcp_mediacrawler_query_dataset",
            "mcp_mediacrawler_generate_report",
        ],
        "counts": {
            "datasets_found": len(listed["datasets"]),
            "contents": normalized["content_count"],
            "comments": normalized["comment_count"],
            "query_results": len(queried["results"]),
        },
        "outputs": {
            "duckdb_path": normalized["duckdb_path"],
            "report_md_path": reported["report_md_path"],
            "report_html_path": reported["report_html_path"],
            "summary_json_path": reported["summary_json_path"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an offline Hermes MCP smoke workflow.")
    parser.add_argument(
        "--home",
        type=Path,
        default=None,
        help="MEDIACRAWLER_MCP_HOME to use. Defaults to a temporary directory.",
    )
    args = parser.parse_args()
    home = args.home or Path(tempfile.mkdtemp(prefix="mediacrawler-mcp-hermes-smoke-"))
    result = run_smoke(home.expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
