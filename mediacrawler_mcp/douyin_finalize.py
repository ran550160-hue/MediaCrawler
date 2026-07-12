from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from mediacrawler_mcp.config import McpConfig, load_config
from mediacrawler_mcp.dataset_bundle_exporter import DatasetBundleExporter
from mediacrawler_mcp.dataset_importer import DatasetImporter
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.normalizer import DatasetNormalizer
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.topic_research import TopicResearchService


def finalize_douyin_collection_task(
    *,
    task_id: str,
    name: str,
    output_dir: str | Path,
    keywords: list[str],
    contents_path: str | Path | None,
    comments_path: str | Path | None = None,
    run_id: str | None = None,
    output_layout: str = "run_isolated",
    crawler_type: str = "search",
    description: str | None = None,
    collection_started_at: str | None = None,
    collection_completed_at: str | None = None,
    dataset_id: str | None = None,
    normalize: bool = True,
    report_type: str = "none",
    config: McpConfig | None = None,
    storage: Storage | None = None,
) -> dict[str, Any]:
    """Export, register, and normalize a Douyin task exactly once per task id."""
    task_id = (task_id or "").strip()
    if not task_id:
        raise McpAppError(ErrorCode.INVALID_ARGUMENT, "collection task_id is required")

    config = config or load_config()
    storage = storage or Storage(config)
    storage.initialize()

    report_type = (report_type or "none").strip().lower()
    if report_type not in {"none", "topic_research"}:
        raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Invalid report type", "report_type must be 'none' or 'topic_research' for Douyin")
    if report_type == "topic_research" and not normalize:
        raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Topic research requires normalization")

    existing = storage.find_dataset_row_by_collection_task_id(task_id, platform="douyin")
    if existing is not None:
        result = _existing_result(existing, task_id=task_id, normalize=normalize, storage=storage)
        return _with_report(result, report_type=report_type, storage=storage)

    if contents_path is None or not str(contents_path).strip():
        raise McpAppError(ErrorCode.INVALID_ARGUMENT, "No Douyin contents JSONL file found for this task")

    exported = DatasetBundleExporter().export_douyin_bundle(
        name=name,
        output_dir=output_dir,
        keywords=keywords,
        contents_path=contents_path,
        comments_path=comments_path,
        run_id=run_id,
        output_layout=output_layout,
        crawler_type=crawler_type,
        description=description,
        collection_task_id=task_id,
        collection_started_at=collection_started_at,
        collection_completed_at=collection_completed_at,
        dataset_id=dataset_id,
    )
    registered = DatasetImporter(config, storage).register_dataset(
        dataset_dir=exported["dataset_dir"],
        import_mode="link",
    )
    normalized = DatasetNormalizer(storage).normalize_dataset(registered["dataset_id"], force=True) if normalize else None

    result = {
        "task_id": task_id,
        "collection_task_id": task_id,
        "dataset_id": registered["dataset_id"],
        "dataset_dir": registered["dataset_dir"],
        "dataset_json_path": registered.get("dataset_json_path"),
        "local_finalize": exported,
        "registered": registered,
        "normalized": normalized,
        "report_type": "none",
        "report": None,
        "already_finalized": False,
    }
    return _with_report(result, report_type=report_type, storage=storage)


def _with_report(result: dict[str, Any], *, report_type: str, storage: Storage) -> dict[str, Any]:
    if report_type == "none":
        return result
    report = TopicResearchService(storage).generate_topic_research_report(result["dataset_id"])
    return {**result, "report_type": "topic_research", "report": report}


def _existing_result(row: dict[str, Any], *, task_id: str, normalize: bool, storage: Storage) -> dict[str, Any]:
    dataset_id = row["dataset_id"]
    dataset_dir = Path(row["dataset_dir"])
    manifest_path = dataset_dir / "dataset.json"
    manifest = _read_manifest(manifest_path)
    metrics = _as_dict(manifest.get("metrics"))
    warnings = _as_list(manifest.get("warnings"))
    registered = {
        "dataset_id": dataset_id,
        "dataset_dir": str(dataset_dir),
        "dataset_json_path": str(manifest_path),
        "import_mode": "existing",
        "warnings": warnings,
    }
    local_finalize = {
        "dataset_id": dataset_id,
        "dataset_dir": str(dataset_dir),
        "dataset_json_path": str(manifest_path),
        "raw_files": _as_dict(_as_dict(manifest.get("files")).get("raw")),
        "metrics": metrics,
        "warnings": warnings,
    }
    normalized = _analysis_summary(dataset_id, dataset_dir, metrics) if normalize else None
    if normalize and normalized is None:
        normalized = DatasetNormalizer(storage).normalize_dataset(dataset_id, force=False)

    return {
        "task_id": task_id,
        "collection_task_id": task_id,
        "dataset_id": dataset_id,
        "dataset_dir": str(dataset_dir),
        "dataset_json_path": str(manifest_path),
        "local_finalize": local_finalize,
        "registered": registered,
        "normalized": normalized,
        "report_type": "none",
        "report": None,
        "already_finalized": True,
    }


def _analysis_summary(dataset_id: str, dataset_dir: Path, metrics: dict[str, Any]) -> dict[str, Any] | None:
    duckdb_path = dataset_dir / "analysis.duckdb"
    if not duckdb_path.exists():
        return None
    try:
        with duckdb.connect(str(duckdb_path), read_only=True) as conn:
            content_count = _scalar(conn, "SELECT COUNT(*) FROM contents WHERE dataset_id = ?", [dataset_id])
            comment_count = _scalar(conn, "SELECT COUNT(*) FROM comments WHERE dataset_id = ?", [dataset_id])
            orphan_count = _scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM comments c
                WHERE c.dataset_id = ?
                  AND COALESCE(c.content_id, '') <> ''
                  AND NOT EXISTS (
                    SELECT 1
                    FROM contents ct
                    WHERE ct.dataset_id = c.dataset_id
                      AND ct.content_id = c.content_id
                  )
                """,
                [dataset_id],
            )
            lineage_count = _scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM comments c
                WHERE c.dataset_id = ?
                  AND lower(COALESCE(c.parent_comment_id, '')) NOT IN ('', '0', 'none', 'null')
                  AND NOT EXISTS (
                    SELECT 1
                    FROM comments parent
                    WHERE parent.dataset_id = c.dataset_id
                      AND parent.comment_id = c.parent_comment_id
                  )
                """,
                [dataset_id],
            )
    except Exception:
        return None

    raw_content_count = _int(metrics.get("content_count"))
    raw_comment_count = _int(metrics.get("comment_count"))
    return {
        "duckdb_path": str(duckdb_path),
        "content_count": content_count,
        "comment_count": comment_count,
        "raw_content_count": raw_content_count,
        "raw_comment_count": raw_comment_count,
        "deduplicated_content_count": max(0, raw_content_count - content_count),
        "deduplicated_comment_count": max(0, raw_comment_count - comment_count),
        "orphan_comment_count": orphan_count,
        "reply_lineage_anomaly_count": lineage_count,
    }


def _scalar(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
