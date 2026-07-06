from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DATASET_TOOL_NAMES = {
    "ping",
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
    "get_report",
}

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

DEFAULT_KEYWORDS = ["AI coding side hustle", "programmer freelance"]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _assert_success(name: str, result: dict[str, Any]) -> None:
    if result.get("status") != "success":
        raise RuntimeError(f"{name} failed: {json.dumps(result, ensure_ascii=False)}")


def _call_tool(steps: list[dict[str, Any]], tool_name: str, func: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    result = func(*args, **kwargs)
    _assert_success(tool_name, result)
    steps.append({"tool": tool_name, "status": "success"})
    return result


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _fixture_contents() -> list[dict[str, Any]]:
    return [
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
            "time": 1700000000000,
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
            "time": "2023-11-14T22:13:21Z",
            "source_keyword": "programmer freelance",
        },
    ]


def _fixture_comments() -> list[dict[str, Any]]:
    return [
        {
            "comment_id": "smoke_comment_1",
            "note_id": "smoke_note_1",
            "nickname": "user-a",
            "content": "Real feedback: pricing and client trust are the hardest parts.",
            "like_count": "12",
            "create_time": 1700000001000,
            "sub_comments": [
                {
                    "comment_id": "smoke_comment_1_1",
                    "nickname": "user-a-reply",
                    "content": "Trust signals and scope control matter before pricing.",
                    "like_count": "3",
                    "create_time": 1700000002000,
                }
            ],
        },
        {
            "comment_id": "smoke_comment_2",
            "note_id": "smoke_note_1",
            "nickname": "user-b",
            "content": "I care more about course quality than income screenshots.",
            "like_count": "8",
            "create_time": "2023-11-14T22:13:23Z",
        },
    ]


def write_fixture_raw_files(raw_dir: Path) -> dict[str, Path]:
    raw_dir = raw_dir.expanduser().resolve()
    contents_path = raw_dir / "xhs_contents.jsonl"
    comments_path = raw_dir / "xhs_comments.jsonl"
    _write_jsonl(contents_path, _fixture_contents())
    _write_jsonl(comments_path, _fixture_comments())
    return {"contents": contents_path, "comments": comments_path}


def create_fixture_bundle(root: Path, dataset_id: str | None = None) -> Path:
    dataset_id = dataset_id or f"hermes_smoke_{_utc_stamp()}_{os.getpid()}"
    bundle_dir = root.expanduser().resolve() / dataset_id
    (bundle_dir / "media").mkdir(parents=True, exist_ok=True)
    (bundle_dir / "logs").mkdir(parents=True, exist_ok=True)
    write_fixture_raw_files(bundle_dir / "raw")
    _write_json(
        bundle_dir / "dataset.json",
        {
            "dataset_id": dataset_id,
            "name": "Hermes MCP smoke fixture",
            "description": "Offline fixture used to verify Hermes MCP dataset analysis.",
            "status": "exported",
            "platforms": ["xhs"],
            "keywords": DEFAULT_KEYWORDS,
            "options": {"source": "hermes_smoke_fixture"},
        },
    )
    return bundle_dir


def _parse_keywords(values: list[str] | None, csv_value: str | None) -> list[str]:
    keywords: list[str] = []
    for value in values or []:
        keywords.extend(part.strip() for part in value.split(",") if part.strip())
    if csv_value:
        keywords.extend(part.strip() for part in csv_value.split(",") if part.strip())
    return keywords or list(DEFAULT_KEYWORDS)


def _load_server(home: Path, force_dataset_profile: bool) -> Any:
    os.environ["MEDIACRAWLER_MCP_HOME"] = str(home)
    if force_dataset_profile:
        os.environ["MEDIACRAWLER_MCP_TOOL_PROFILE"] = "dataset"
        os.environ["MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION"] = "false"
    else:
        os.environ.setdefault("MEDIACRAWLER_MCP_TOOL_PROFILE", "dataset")
        os.environ.setdefault("MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION", "false")

    import mediacrawler_mcp.server as server  # noqa: PLC0415

    return importlib.reload(server)


def _tool_names(server_module: Any) -> set[str]:
    return {tool.name for tool in asyncio.run(server_module.mcp.list_tools())}


def _check_dataset_profile(server_module: Any, steps: list[dict[str, Any]]) -> dict[str, Any]:
    names = _tool_names(server_module)
    missing = sorted(DATASET_TOOL_NAMES - names)
    visible_experimental = sorted(EXPERIMENTAL_TOOL_NAMES & names)
    if missing or visible_experimental:
        raise RuntimeError(
            "dataset profile tool visibility check failed: "
            + json.dumps(
                {
                    "missing_dataset_tools": missing,
                    "visible_experimental_tools": visible_experimental,
                },
                ensure_ascii=False,
            )
        )
    steps.append({"tool": "mcp.list_tools", "status": "success"})
    return {
        "dataset_tools": sorted(DATASET_TOOL_NAMES),
        "experimental_tools_hidden": True,
    }


def _run_bundle_import(
    server: Any,
    steps: list[dict[str, Any]],
    dataset_dir: Path,
    import_mode: str,
) -> tuple[str, str, dict[str, Any]]:
    validation = _call_tool(steps, "validate_dataset_bundle", server.validate_dataset_bundle, str(dataset_dir))
    if not validation.get("valid"):
        raise RuntimeError(f"validate_dataset_bundle returned invalid: {json.dumps(validation, ensure_ascii=False)}")
    registered = _call_tool(steps, "register_dataset", server.register_dataset, str(dataset_dir), import_mode)
    return registered["dataset_id"], registered["dataset_dir"], {
        "validation": {
            "warnings": validation.get("warnings", []),
            "errors": validation.get("errors", []),
        },
        "import_mode": import_mode,
    }


def _run_raw_file_import(
    server: Any,
    steps: list[dict[str, Any]],
    *,
    name: str,
    keywords: list[str],
    contents_path: Path | None,
    comments_path: Path | None,
    source_keyword: str | None,
) -> tuple[str, str, dict[str, Any]]:
    created = _call_tool(
        steps,
        "create_dataset",
        server.create_dataset,
        name=name,
        platforms=["xhs"],
        keywords=keywords,
        description="Hermes MCP smoke dataset created before import_raw_files.",
    )
    imported = _call_tool(
        steps,
        "import_raw_files",
        server.import_raw_files,
        dataset_id=created["dataset_id"],
        platform="xhs",
        contents_path=str(contents_path) if contents_path else None,
        comments_path=str(comments_path) if comments_path else None,
        source_keyword=source_keyword,
    )
    synced = _call_tool(steps, "sync_dataset_manifest", server.sync_dataset_manifest, created["dataset_id"])
    return created["dataset_id"], created["dataset_dir"], {
        "imported_raw_files": imported.get("raw_files", {}),
        "manifest_metrics": synced.get("metrics", {}),
    }


def run_smoke(
    home: Path,
    *,
    dataset_dir: Path | None = None,
    contents_path: Path | None = None,
    comments_path: Path | None = None,
    import_mode: str = "copy",
    name: str = "Hermes MCP smoke dataset",
    keywords: list[str] | None = None,
    source_keyword: str | None = None,
    query: str | None = None,
    target: str = "auto",
    limit: int = 5,
    top_n: int = 5,
    force_dataset_profile: bool = True,
) -> dict[str, Any]:
    if dataset_dir and (contents_path or comments_path):
        raise ValueError("Use either dataset_dir or raw contents/comments paths, not both.")
    if import_mode not in {"copy", "link"}:
        raise ValueError("import_mode must be 'copy' or 'link'.")
    if target not in {"auto", "comments", "contents"}:
        raise ValueError("target must be 'auto', 'comments', or 'contents'.")

    home = home.expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    keywords = keywords or list(DEFAULT_KEYWORDS)
    source_keyword = source_keyword or keywords[0]
    steps: list[dict[str, Any]] = []
    server = _load_server(home, force_dataset_profile=force_dataset_profile)
    profile = _check_dataset_profile(server, steps)

    ping_result = server.ping("hermes")
    if ping_result != "pong:hermes":
        raise RuntimeError(f"ping failed: {ping_result}")
    steps.append({"tool": "ping", "status": "success"})

    fixture_bundle_dir: Path | None = None
    mode: str
    import_details: dict[str, Any]
    if dataset_dir:
        mode = "bundle"
        dataset_id, registered_dir, import_details = _run_bundle_import(
            server,
            steps,
            dataset_dir.expanduser().resolve(),
            import_mode,
        )
    elif contents_path or comments_path:
        mode = "raw_files"
        dataset_id, registered_dir, import_details = _run_raw_file_import(
            server,
            steps,
            name=name,
            keywords=keywords,
            contents_path=contents_path.expanduser().resolve() if contents_path else None,
            comments_path=comments_path.expanduser().resolve() if comments_path else None,
            source_keyword=source_keyword,
        )
    else:
        mode = "fixture_bundle"
        fixture_bundle_dir = create_fixture_bundle(home / "smoke_inbox")
        dataset_id, registered_dir, import_details = _run_bundle_import(
            server,
            steps,
            fixture_bundle_dir,
            import_mode,
        )

    listed = _call_tool(steps, "list_datasets", server.list_datasets, platform="xhs", limit=100)
    fetched = _call_tool(steps, "get_dataset", server.get_dataset, dataset_id)
    normalized = _call_tool(steps, "normalize_dataset", server.normalize_dataset, dataset_id, True)

    query_target = target
    if query_target == "auto":
        query_target = "comments" if normalized["comment_count"] > 0 else "contents"
    sort_by = "like_count" if query_target == "comments" else "engagement_count"
    query_text = "" if query is None else query
    queried = _call_tool(
        steps,
        "query_dataset",
        server.query_dataset,
        dataset_id=dataset_id,
        query=query_text,
        target=query_target,
        platform="xhs",
        source_keyword=None,
        limit=limit,
        sort_by=sort_by,
    )
    reported = _call_tool(steps, "generate_report", server.generate_report, dataset_id, "topic_research", top_n)
    latest_report = _call_tool(steps, "get_report", server.get_report, dataset_id)

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
        "mode": mode,
        "home": str(home),
        "dataset_id": dataset_id,
        "dataset_dir": registered_dir,
        "source_dataset_dir": str(dataset_dir) if dataset_dir else str(fixture_bundle_dir) if fixture_bundle_dir else None,
        "profile": profile,
        "steps": steps,
        "tool_name_hint": [
            "mcp_mediacrawler_validate_dataset_bundle",
            "mcp_mediacrawler_register_dataset",
            "mcp_mediacrawler_import_raw_files",
            "mcp_mediacrawler_sync_dataset_manifest",
            "mcp_mediacrawler_normalize_dataset",
            "mcp_mediacrawler_query_dataset",
            "mcp_mediacrawler_generate_report",
            "mcp_mediacrawler_get_report",
        ],
        "counts": {
            "datasets_found": len(listed["datasets"]),
            "contents": normalized["content_count"],
            "comments": normalized["comment_count"],
            "query_results": len(queried["results"]),
        },
        "query": {
            "target": query_target,
            "query": query_text,
            "sort_by": sort_by,
            "limit": limit,
        },
        "import": import_details,
        "outputs": {
            "duckdb_path": normalized["duckdb_path"],
            "report_md_path": reported["report_md_path"],
            "report_html_path": reported["report_html_path"],
            "summary_json_path": reported["summary_json_path"],
            "latest_report_path": latest_report["report"]["report_md_path"],
        },
        "dataset": fetched["dataset"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an offline Hermes MCP dataset-profile smoke workflow.")
    parser.add_argument(
        "--home",
        type=Path,
        default=None,
        help="MEDIACRAWLER_MCP_HOME to use. Defaults to a temporary directory.",
    )
    parser.add_argument("--dataset-dir", type=Path, default=None, help="Existing desktop-exported dataset bundle.")
    parser.add_argument("--contents", type=Path, default=None, help="Raw xhs contents JSONL file for import_raw_files.")
    parser.add_argument("--comments", type=Path, default=None, help="Raw xhs comments JSONL file for import_raw_files.")
    parser.add_argument("--import-mode", choices=["copy", "link"], default="copy", help="register_dataset import mode.")
    parser.add_argument("--name", default="Hermes MCP smoke dataset", help="Dataset name for raw-file imports.")
    parser.add_argument("--keyword", action="append", help="Dataset keyword. Can be repeated or comma-separated.")
    parser.add_argument("--keywords", default=None, help="Comma-separated dataset keywords.")
    parser.add_argument("--source-keyword", default=None, help="source_keyword metadata for raw-file imports.")
    parser.add_argument("--query", default=None, help="Optional query text. Defaults to an empty query.")
    parser.add_argument("--target", choices=["auto", "comments", "contents"], default="auto", help="Query target.")
    parser.add_argument("--limit", type=int, default=5, help="query_dataset result limit.")
    parser.add_argument("--top-n", type=int, default=5, help="generate_report top_n.")
    parser.add_argument(
        "--respect-profile-env",
        action="store_true",
        help="Do not force dataset profile env before importing server.py; useful for catching a bad shell env.",
    )
    args = parser.parse_args()

    if args.dataset_dir and (args.contents or args.comments):
        parser.error("--dataset-dir cannot be combined with --contents/--comments")

    home = args.home or Path(tempfile.mkdtemp(prefix="mediacrawler-mcp-hermes-smoke-"))
    result = run_smoke(
        home,
        dataset_dir=args.dataset_dir,
        contents_path=args.contents,
        comments_path=args.comments,
        import_mode=args.import_mode,
        name=args.name,
        keywords=_parse_keywords(args.keyword, args.keywords),
        source_keyword=args.source_keyword,
        query=args.query,
        target=args.target,
        limit=args.limit,
        top_n=args.top_n,
        force_dataset_profile=not args.respect_profile_env,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
