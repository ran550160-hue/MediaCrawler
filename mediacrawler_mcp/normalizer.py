from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.utils import utc_now_iso


CONTENT_COLUMNS = (
    "dataset_id",
    "platform",
    "source_keyword",
    "content_id",
    "author_id",
    "author_name",
    "title",
    "desc",
    "content_text",
    "url",
    "publish_time",
    "like_count",
    "comment_count",
    "share_count",
    "collect_count",
    "engagement_count",
    "crawl_time",
    "raw_json",
)

COMMENT_COLUMNS = (
    "dataset_id",
    "platform",
    "source_keyword",
    "content_id",
    "comment_id",
    "parent_comment_id",
    "user_id",
    "user_name",
    "comment_text",
    "like_count",
    "publish_time",
    "crawl_time",
    "raw_json",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _number(value: Any) -> int:
    if value is None:
        return 0
    text = str(value).replace(",", "").strip()
    if not text:
        return 0
    multipliers = (("万", 10000), ("w", 10000), ("W", 10000), ("千", 1000), ("k", 1000), ("K", 1000))
    for suffix, multiplier in multipliers:
        if text.endswith(suffix):
            try:
                return int(float(text[: -len(suffix)]) * multiplier)
            except ValueError:
                return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise McpAppError(
                    ErrorCode.NORMALIZE_FAILED,
                    "Invalid JSONL input",
                    f"{path}:{line_no}: {exc}",
                ) from exc
            if not isinstance(value, dict):
                raise McpAppError(
                    ErrorCode.NORMALIZE_FAILED,
                    "JSONL rows must be objects",
                    f"{path}:{line_no}",
                )
            rows.append(value)
    return rows


def _normalize_content(dataset_id: str, item: dict[str, Any]) -> dict[str, Any]:
    like_count = _number(item.get("liked_count") or item.get("like_count"))
    comment_count = _number(item.get("comment_count"))
    share_count = _number(item.get("share_count"))
    collect_count = _number(item.get("collected_count") or item.get("collect_count"))
    raw_json = json.dumps(item, ensure_ascii=False)
    return {
        "dataset_id": dataset_id,
        "platform": "xhs",
        "source_keyword": _text(item.get("source_keyword")),
        "content_id": _text(item.get("note_id")),
        "author_id": _text(item.get("user_id")),
        "author_name": _text(item.get("nickname") or item.get("user_nickname")),
        "title": _text(item.get("title")),
        "desc": _text(item.get("desc")),
        "content_text": _text(item.get("content") or item.get("desc")),
        "url": _text(item.get("note_url")),
        "publish_time": _text(item.get("time") or item.get("publish_time")),
        "like_count": like_count,
        "comment_count": comment_count,
        "share_count": share_count,
        "collect_count": collect_count,
        "engagement_count": like_count + comment_count + share_count + collect_count,
        "crawl_time": _text(item.get("last_modify_ts") or item.get("crawl_time") or utc_now_iso()),
        "raw_json": raw_json,
    }


def _normalize_comment(dataset_id: str, item: dict[str, Any]) -> dict[str, Any]:
    raw_json = json.dumps(item, ensure_ascii=False)
    return {
        "dataset_id": dataset_id,
        "platform": "xhs",
        "source_keyword": _text(item.get("source_keyword")),
        "content_id": _text(item.get("note_id")),
        "comment_id": _text(item.get("comment_id")),
        "parent_comment_id": _text(item.get("parent_comment_id")),
        "user_id": _text(item.get("user_id")),
        "user_name": _text(item.get("nickname") or item.get("user_nickname")),
        "comment_text": _text(item.get("content")),
        "like_count": _number(item.get("like_count")),
        "publish_time": _text(item.get("create_time") or item.get("publish_time")),
        "crawl_time": _text(item.get("last_modify_ts") or item.get("crawl_time") or utc_now_iso()),
        "raw_json": raw_json,
    }


def _create_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contents (
          dataset_id TEXT,
          platform TEXT,
          source_keyword TEXT,
          content_id TEXT,
          author_id TEXT,
          author_name TEXT,
          title TEXT,
          "desc" TEXT,
          content_text TEXT,
          url TEXT,
          publish_time TEXT,
          like_count BIGINT,
          comment_count BIGINT,
          share_count BIGINT,
          collect_count BIGINT,
          engagement_count BIGINT,
          crawl_time TEXT,
          raw_json JSON
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
          dataset_id TEXT,
          platform TEXT,
          source_keyword TEXT,
          content_id TEXT,
          comment_id TEXT,
          parent_comment_id TEXT,
          user_id TEXT,
          user_name TEXT,
          comment_text TEXT,
          like_count BIGINT,
          publish_time TEXT,
          crawl_time TEXT,
          raw_json JSON
        )
        """
    )


def _insert_rows(conn: duckdb.DuckDBPyConnection, table: str, columns: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    placeholders = ", ".join(["?"] * len(columns))
    quoted_columns = ", ".join(f'"{column}"' if column == "desc" else column for column in columns)
    values = [tuple(row[column] for column in columns) for row in rows]
    conn.executemany(f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders})", values)


def _dedupe_contents(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    blanks = []
    for row in rows:
        content_id = row["content_id"].strip()
        if not content_id:
            blanks.append(row)
            continue
        current = by_id.get(content_id)
        if current is None or row["engagement_count"] >= current["engagement_count"]:
            by_id[content_id] = row
    return list(by_id.values()) + blanks


def _dedupe_comments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    blanks = []
    for row in rows:
        comment_id = row["comment_id"].strip()
        if not comment_id:
            blanks.append(row)
            continue
        current = by_id.get(comment_id)
        if current is None or row["like_count"] >= current["like_count"]:
            by_id[comment_id] = row
    return list(by_id.values()) + blanks


class DatasetNormalizer:
    def __init__(self, storage: Storage):
        self.storage = storage

    def normalize_dataset(self, dataset_id: str, force: bool = False) -> dict[str, Any]:
        dataset_id = (dataset_id or "").strip()
        if not dataset_id:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Dataset ID is required")

        self.storage.initialize()
        row = self.storage.get_dataset_row(dataset_id)
        if row is None:
            raise McpAppError(ErrorCode.DATASET_NOT_FOUND, "Dataset not found", f"dataset_id={dataset_id}")

        dataset_dir = Path(row["dataset_dir"])
        raw_dir = dataset_dir / "raw"
        contents_path = raw_dir / "xhs_contents.jsonl"
        comments_path = raw_dir / "xhs_comments.jsonl"
        if not contents_path.exists() and not comments_path.exists():
            raise McpAppError(
                ErrorCode.NORMALIZE_FAILED,
                "No raw xhs JSONL files found",
                f"Expected {contents_path} or {comments_path}",
            )

        content_rows = [_normalize_content(dataset_id, item) for item in _read_jsonl(contents_path)] if contents_path.exists() else []
        comment_rows = [_normalize_comment(dataset_id, item) for item in _read_jsonl(comments_path)] if comments_path.exists() else []
        content_rows = _dedupe_contents(content_rows)
        comment_rows = _dedupe_comments(comment_rows)

        duckdb_path = dataset_dir / "analysis.duckdb"
        if duckdb_path.exists() and force:
            duckdb_path.unlink()

        with duckdb.connect(str(duckdb_path)) as conn:
            _create_tables(conn)
            conn.execute("DELETE FROM contents WHERE dataset_id = ?", [dataset_id])
            conn.execute("DELETE FROM comments WHERE dataset_id = ?", [dataset_id])
            _insert_rows(conn, "contents", CONTENT_COLUMNS, content_rows)
            _insert_rows(conn, "comments", COMMENT_COLUMNS, comment_rows)

        return {
            "duckdb_path": str(duckdb_path),
            "content_count": len(content_rows),
            "comment_count": len(comment_rows),
        }
