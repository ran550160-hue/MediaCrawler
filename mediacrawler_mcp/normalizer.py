from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.dataset_importer import RAW_FILE_NAMES
from mediacrawler_mcp.utils import strip_sensitive_url_params, utc_now_iso


CONTENT_COLUMNS = (
    "dataset_id",
    "platform",
    "collection_task_id",
    "source_keyword",
    "content_id",
    "content_type",
    "author_id",
    "author_name",
    "title",
    "desc",
    "content_text",
    "tags",
    "url",
    "publish_time",
    "publish_datetime",
    "like_count",
    "comment_count",
    "share_count",
    "collect_count",
    "engagement_count",
    "interaction_field_status",
    "interaction_approximate_fields",
    "interaction_parse_error_fields",
    "crawl_time",
    "raw_json",
)

TAG_PATTERN = re.compile(r"#([^#\s\[]+)(?:\[话题\])?#?")
TOPIC_MARKER_PATTERN = re.compile(r"\[话题\]")
WHITESPACE_PATTERN = re.compile(r"\s+")

LIKE_PATHS = (
    ("interact_info", "liked_count"), ("note", "interact_info", "liked_count"), ("note_card", "interact_info", "liked_count"),
    ("liked_count",), ("like_count",), ("likedCount",), ("likeCount",), ("likes",), ("likes_count",), ("like_num",), ("liked_num",), ("likeNum",),
)
COMMENT_PATHS = (
    ("interact_info", "comment_count"), ("note", "interact_info", "comment_count"), ("note_card", "interact_info", "comment_count"),
    ("comment_count",), ("comments_count",), ("commentCount",), ("commentsCount",), ("comments",), ("comment_num",),
)
SHARE_PATHS = (
    ("interact_info", "share_count"), ("note", "interact_info", "share_count"), ("note_card", "interact_info", "share_count"),
    ("share_count",), ("shares",), ("shareCount",), ("shares_count",), ("share_num",),
)
COLLECT_PATHS = (
    ("interact_info", "collected_count"), ("note", "interact_info", "collected_count"), ("note_card", "interact_info", "collected_count"),
    ("collected_count",), ("collect_count",), ("collectedCount",), ("collectCount",), ("favorite_count",), ("favoriteCount",), ("favorites",), ("collect_num",), ("collected_num",),
)
KNOWN_INTERACTION_PATHS = LIKE_PATHS + COMMENT_PATHS + SHARE_PATHS + COLLECT_PATHS

COMMENT_COLUMNS = (
    "dataset_id",
    "platform",
    "collection_task_id",
    "source_keyword",
    "content_id",
    "comment_id",
    "parent_comment_id",
    "user_id",
    "user_name",
    "comment_text",
    "like_count",
    "publish_time",
    "publish_datetime",
    "crawl_time",
    "raw_json",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _first_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _first_number(item: dict[str, Any], *keys: str) -> int:
    first_value = None
    for key in keys:
        value = item.get(key)
        if value is None or str(value).strip() == "":
            continue
        if first_value is None:
            first_value = value
        number = _number(value)
        if number != 0:
            return number
    return _number(first_value)


def _path_value(item: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = item
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _first_number_paths(item: dict[str, Any], *paths: tuple[str, ...]) -> int:
    """Return the first non-zero numeric value across raw response shapes.

    XHS search rows and note-detail rows expose interaction fields at different
    nesting levels. Keeping the paths here makes the precedence explicit and
    makes a zero a fallback rather than silently discarding a later real value.
    """
    first_value = None
    for path in paths:
        value = _path_value(item, path)
        if value is None or str(value).strip() == "":
            continue
        if first_value is None:
            first_value = value
        number = _number(value)
        if number != 0:
            return number
    return _number(first_value)


def _number(value: Any) -> int:
    number, _, _ = _number_with_status(value)
    return number


def _number_with_status(value: Any) -> tuple[int, bool, bool]:
    if value is None:
        return 0, False, False
    text = str(value).replace(",", "").strip()
    if not text:
        return 0, False, False
    approximate = text.endswith("+")
    if approximate:
        text = text[:-1].strip()
    if not text:
        return 0, False, approximate
    multipliers = (("万", 10000), ("w", 10000), ("W", 10000), ("千", 1000), ("k", 1000), ("K", 1000))
    for suffix, multiplier in multipliers:
        if text.endswith(suffix):
            try:
                return int(float(text[: -len(suffix)]) * multiplier), True, approximate
            except ValueError:
                return 0, False, approximate
    try:
        return int(float(text)), True, approximate
    except ValueError:
        return 0, False, approximate


def _interaction_metadata(item: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """Expose approximate and failed fields instead of silently collapsing them to zero."""
    fields = {"like_count": LIKE_PATHS, "comment_count": COMMENT_PATHS, "share_count": SHARE_PATHS, "collect_count": COLLECT_PATHS}
    found = False
    parsed_values: list[int] = []
    approximate_fields: list[str] = []
    parse_error_fields: list[str] = []
    for field, paths in fields.items():
        values = [_path_value(item, path) for path in paths]
        values = [value for value in values if value is not None and str(value).strip() != ""]
        if not values:
            continue
        found = True
        results = [_number_with_status(value) for value in values]
        parsed_values.extend(number for number, ok, _ in results if ok)
        if any(approximate and ok for _, ok, approximate in results):
            approximate_fields.append(field)
        if any(not ok for _, ok, _ in results):
            parse_error_fields.append(field)
    if not found:
        status = "missing"
    elif not parsed_values:
        status = "parse_error"
    elif parse_error_fields:
        status = "partial_parse_error"
    elif approximate_fields:
        status = "present_approximate"
    elif all(value == 0 for value in parsed_values):
        status = "present_zero"
    else:
        status = "present"
    return status, approximate_fields, parse_error_fields


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        timestamp = float(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _collapse_spaces(value: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", value).strip()


def _dedupe_repeated_text(value: str) -> str:
    text = _collapse_spaces(value)
    if not text:
        return ""
    tokens = text.split(" ")
    if len(tokens) > 1 and len(tokens) % 2 == 0:
        midpoint = len(tokens) // 2
        if tokens[:midpoint] == tokens[midpoint:]:
            return " ".join(tokens[:midpoint])
    if len(text) % 2 == 0:
        midpoint = len(text) // 2
        if text[:midpoint] == text[midpoint:]:
            return text[:midpoint]
    return text


def _clean_topic_text(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    without_tags = TAG_PATTERN.sub(" ", text)
    without_markers = TOPIC_MARKER_PATTERN.sub("", without_tags)
    return _dedupe_repeated_text(without_markers)


def _clean_tag(value: Any) -> str:
    text = TOPIC_MARKER_PATTERN.sub("", _text(value)).strip().strip("#").strip()
    return text


def _unique_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    unique = []
    for tag in tags:
        cleaned = _clean_tag(tag)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)
    return unique


def _extract_tags_from_text(*values: Any) -> list[str]:
    tags: list[str] = []
    for value in values:
        text = _text(value)
        if not text:
            continue
        tags.extend(match.group(1) for match in TAG_PATTERN.finditer(text))
    return _unique_tags(tags)


def _extract_plain_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        tags: list[str] = []
        for key in ("name", "tag_name", "tag", "title", "text"):
            if key in value:
                tags.extend(_extract_plain_tags(value[key]))
        return _unique_tags(tags)
    if isinstance(value, (list, tuple, set)):
        tags: list[str] = []
        for item in value:
            tags.extend(_extract_plain_tags(item))
        return _unique_tags(tags)
    return _unique_tags([_text(value)])


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


def _normalize_content(dataset_id: str, item: dict[str, Any], collection_task_id: str) -> dict[str, Any]:
    like_count = _first_number_paths(item, *LIKE_PATHS)
    comment_count = _first_number_paths(item, *COMMENT_PATHS)
    share_count = _first_number_paths(item, *SHARE_PATHS)
    collect_count = _first_number_paths(item, *COLLECT_PATHS)
    publish_time = _first_value(item, "time", "publish_time", "create_time", "last_update_time")
    raw_title = item.get("title")
    raw_desc = _first_value(item, "desc", "description")
    raw_content = _first_value(item, "content", "content_text", "desc", "description")
    tags = _unique_tags(
        _extract_tags_from_text(raw_title, raw_desc, raw_content)
        + _extract_plain_tags(_first_value(item, "tags", "tag_list", "topic_list", "hash_tags"))
    )
    raw_json = json.dumps(item, ensure_ascii=False)
    interaction_status, approximate_fields, parse_error_fields = _interaction_metadata(item)
    return {
        "dataset_id": dataset_id,
        "platform": "xhs",
        "collection_task_id": collection_task_id,
        "source_keyword": _text(item.get("source_keyword")),
        "content_id": _text(_first_value(item, "note_id", "content_id", "id")),
        "content_type": _text(_first_value(item, "type", "note_type", "content_type")),
        "author_id": _text(item.get("user_id")),
        "author_name": _text(item.get("nickname") or item.get("user_nickname")),
        "title": _clean_topic_text(raw_title),
        "desc": _clean_topic_text(raw_desc),
        "content_text": _clean_topic_text(raw_content),
        "tags": json.dumps(tags, ensure_ascii=False),
        "url": _text(item.get("note_url")),
        "publish_time": _text(publish_time),
        "publish_datetime": _datetime(publish_time),
        "like_count": like_count,
        "comment_count": comment_count,
        "share_count": share_count,
        "collect_count": collect_count,
        "engagement_count": like_count + comment_count + share_count + collect_count,
        "interaction_field_status": interaction_status,
        "interaction_approximate_fields": json.dumps(approximate_fields, ensure_ascii=False),
        "interaction_parse_error_fields": json.dumps(parse_error_fields, ensure_ascii=False),
        "crawl_time": _text(item.get("last_modify_ts") or item.get("crawl_time") or utc_now_iso()),
        "raw_json": raw_json,
    }


def _comment_children(item: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("sub_comments", "sub_comment_list", "comments", "replies"):
        value = item.get(key)
        if isinstance(value, list):
            return [child for child in value if isinstance(child, dict)]
    return []


def _flatten_comment_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    stack = list(items)
    while stack:
        item = stack.pop(0)
        flattened.append(item)
        for child in _comment_children(item):
            child = dict(child)
            child.setdefault("note_id", item.get("note_id"))
            child.setdefault("source_keyword", item.get("source_keyword"))
            child.setdefault("parent_comment_id", item.get("comment_id"))
            stack.append(child)
    return flattened


def _normalize_comment(
    dataset_id: str,
    item: dict[str, Any],
    source_keyword_by_content: dict[str, str],
    collection_task_id: str,
) -> dict[str, Any]:
    content_id = _text(_first_value(item, "note_id", "content_id", "aweme_id"))
    publish_time = _first_value(item, "create_time", "publish_time", "time")
    raw_json = json.dumps(item, ensure_ascii=False)
    return {
        "dataset_id": dataset_id,
        "platform": "xhs",
        "collection_task_id": collection_task_id,
        "source_keyword": _text(item.get("source_keyword")) or source_keyword_by_content.get(content_id, ""),
        "content_id": content_id,
        "comment_id": _text(_first_value(item, "comment_id", "id", "commentId")),
        "parent_comment_id": _text(_first_value(item, "parent_comment_id", "parent_id", "parentCommentId")),
        "user_id": _text(item.get("user_id")),
        "user_name": _text(item.get("nickname") or item.get("user_nickname")),
        "comment_text": _text(_first_value(item, "content", "comment_text", "comment_content", "text")),
        "like_count": _number(_first_value(item, "like_count", "liked_count", "like_num")),
        "publish_time": _text(publish_time),
        "publish_datetime": _datetime(publish_time),
        "crawl_time": _text(item.get("last_modify_ts") or item.get("crawl_time") or utc_now_iso()),
        "raw_json": raw_json,
    }


# ---------------------------------------------------------------------------
# Douyin normalization
# ---------------------------------------------------------------------------

# aweme_type raw values explicitly mapped to common content_type labels.
# Only values covered by verified code or real fixtures belong here;
# everything else degrades to ``douyin_aweme_<raw_value>``.
_DOUYIN_AWEME_TYPE_MAP: dict[str, str] = {
    "0": "video",
}

_FIRST_LEVEL_PARENT_VALUES = frozenset({"", "0", "none", "null"})


def _douyin_content_type(raw_aweme_type: Any) -> str:
    raw_text = _text(raw_aweme_type).strip()
    if not raw_text:
        return "douyin_aweme_unknown"
    mapped = _DOUYIN_AWEME_TYPE_MAP.get(raw_text)
    if mapped is not None:
        return mapped
    return f"douyin_aweme_{raw_text}"


def _douyin_url(item: dict[str, Any]) -> str:
    raw_url = _first_value(item, "aweme_url", "note_url", "url")
    aweme_id = _text(_first_value(item, "aweme_id", "content_id", "id"))
    if raw_url:
        return strip_sensitive_url_params(_text(raw_url)) or ""
    if aweme_id:
        return f"https://www.douyin.com/video/{aweme_id}"
    return ""


_DOUYIN_INTERACTION_FIELDS = (
    ("like_count", ("liked_count", "like_count")),
    ("comment_count", ("comment_count",)),
    ("share_count", ("share_count",)),
    ("collect_count", ("collected_count", "collect_count")),
)


def _douyin_interaction_metadata(item: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """Same status taxonomy as XHS but keyed on flat Douyin field names."""
    parsed_values: list[int] = []
    approximate_fields: list[str] = []
    parse_error_fields: list[str] = []
    found_any = False
    for field, keys in _DOUYIN_INTERACTION_FIELDS:
        value = _first_value(item, *keys)
        if value is None or str(value).strip() == "":
            continue
        found_any = True
        number, ok, approximate = _number_with_status(value)
        if ok:
            parsed_values.append(number)
        if approximate and ok:
            approximate_fields.append(field)
        if not ok:
            parse_error_fields.append(field)
    if not found_any:
        status = "missing"
    elif not parsed_values:
        status = "parse_error"
    elif parse_error_fields:
        status = "partial_parse_error"
    elif approximate_fields:
        status = "present_approximate"
    elif all(value == 0 for value in parsed_values):
        status = "present_zero"
    else:
        status = "present"
    return status, approximate_fields, parse_error_fields


def _normalize_douyin_content(dataset_id: str, item: dict[str, Any], collection_task_id: str) -> dict[str, Any]:
    like_count = _number(item.get("liked_count"))
    comment_count = _number(item.get("comment_count"))
    share_count = _number(item.get("share_count"))
    collect_count = _number(item.get("collected_count"))
    publish_time = _first_value(item, "create_time", "publish_time")
    raw_title = item.get("title")
    raw_desc = _first_value(item, "desc", "description")
    raw_content = _first_value(item, "content", "content_text", "desc", "description", "title")
    raw_aweme_type = item.get("aweme_type")
    # Douyin raw rows do not carry hashtags/challenges lists; extract from text.
    tags = _extract_tags_from_text(raw_title, raw_desc, raw_content)
    interaction_status, approximate_fields, parse_error_fields = _douyin_interaction_metadata(item)
    return {
        "dataset_id": dataset_id,
        "platform": "douyin",
        "collection_task_id": collection_task_id,
        "source_keyword": _text(item.get("source_keyword")),
        "content_id": _text(_first_value(item, "aweme_id", "content_id", "id")),
        "content_type": _douyin_content_type(raw_aweme_type),
        "author_id": _text(item.get("user_id")),
        "author_name": _text(item.get("nickname") or item.get("user_nickname")),
        "title": _clean_topic_text(raw_title),
        "desc": _clean_topic_text(raw_desc),
        "content_text": _clean_topic_text(raw_content),
        "tags": json.dumps(tags, ensure_ascii=False),
        "url": _douyin_url(item),
        "publish_time": _text(publish_time),
        "publish_datetime": _datetime(publish_time),
        "like_count": like_count,
        "comment_count": comment_count,
        "share_count": share_count,
        "collect_count": collect_count,
        "engagement_count": like_count + comment_count + share_count + collect_count,
        "interaction_field_status": interaction_status,
        "interaction_approximate_fields": json.dumps(approximate_fields, ensure_ascii=False),
        "interaction_parse_error_fields": json.dumps(parse_error_fields, ensure_ascii=False),
        "crawl_time": _text(item.get("last_modify_ts") or item.get("crawl_time") or utc_now_iso()),
        "raw_json": json.dumps(item, ensure_ascii=False),
    }


def _normalize_douyin_parent_comment_id(raw_value: Any) -> str | None:
    """Normalize Douyin ``parent_comment_id``.

    Raw values of ``"0"`` / ``0`` / ``null`` / empty string mean a first-level
    comment and become ``None``.  Any other non-zero value is retained as a
    string ID for second-level replies.
    """
    if raw_value is None:
        return None
    text_value = _text(raw_value).strip()
    if not text_value or text_value.lower() in _FIRST_LEVEL_PARENT_VALUES:
        return None
    return text_value


def _normalize_douyin_comment(
    dataset_id: str,
    item: dict[str, Any],
    source_keyword_by_content: dict[str, str],
    collection_task_id: str,
) -> dict[str, Any]:
    content_id = _text(_first_value(item, "aweme_id", "content_id", "note_id"))
    publish_time = _first_value(item, "create_time", "publish_time", "time")
    return {
        "dataset_id": dataset_id,
        "platform": "douyin",
        "collection_task_id": collection_task_id,
        # raw comments never carry source_keyword; inherit via aweme_id.
        "source_keyword": _text(item.get("source_keyword")) or source_keyword_by_content.get(content_id, ""),
        "content_id": content_id,
        "comment_id": _text(_first_value(item, "comment_id", "id", "commentId")),
        "parent_comment_id": _normalize_douyin_parent_comment_id(item.get("parent_comment_id")),
        "user_id": _text(item.get("user_id")),
        "user_name": _text(item.get("nickname") or item.get("user_nickname")),
        "comment_text": _text(_first_value(item, "content", "comment_text", "comment_content", "text")),
        "like_count": _number(_first_value(item, "like_count", "liked_count", "like_num")),
        "publish_time": _text(publish_time),
        "publish_datetime": _datetime(publish_time),
        "crawl_time": _text(item.get("last_modify_ts") or item.get("crawl_time") or utc_now_iso()),
        "raw_json": json.dumps(item, ensure_ascii=False),
    }


def _normalize_diagnostics(
    content_rows: list[dict[str, Any]], comment_rows: list[dict[str, Any]]
) -> tuple[int, int]:
    """Count orphan comments and reply-lineage anomalies.

    An orphan comment has ``content_id`` not present among normalized content
    rows.  A reply-lineage anomaly is a second-level comment whose
    ``parent_comment_id`` does not match any known ``comment_id``; first-level
    sentinel values (``None``/``"0"``/empty) are never flagged.
    """
    content_ids = {row["content_id"] for row in content_rows if row.get("content_id")}
    comment_ids = {row["comment_id"] for row in comment_rows if row.get("comment_id")}
    orphan_count = 0
    lineage_anomaly_count = 0
    for row in comment_rows:
        content_id = row.get("content_id") or ""
        if content_id and content_id not in content_ids:
            orphan_count += 1
        parent = row.get("parent_comment_id")
        if parent is None:
            continue
        parent_text = _text(parent).strip()
        if not parent_text or parent_text.lower() in _FIRST_LEVEL_PARENT_VALUES:
            continue
        if parent_text not in comment_ids:
            lineage_anomaly_count += 1
    return orphan_count, lineage_anomaly_count


def _create_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("DROP TABLE IF EXISTS contents")
    conn.execute("DROP TABLE IF EXISTS comments")
    conn.execute(
        """
        CREATE TABLE contents (
          dataset_id TEXT,
          platform TEXT,
          collection_task_id TEXT,
          source_keyword TEXT,
          content_id TEXT,
          content_type TEXT,
          author_id TEXT,
          author_name TEXT,
          title TEXT,
          "desc" TEXT,
          content_text TEXT,
          tags TEXT,
          url TEXT,
          publish_time TEXT,
          publish_datetime TIMESTAMP,
          like_count BIGINT,
          comment_count BIGINT,
          share_count BIGINT,
          collect_count BIGINT,
          engagement_count BIGINT,
          interaction_field_status TEXT,
          interaction_approximate_fields TEXT,
          interaction_parse_error_fields TEXT,
          crawl_time TEXT,
          raw_json JSON
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE comments (
          dataset_id TEXT,
          platform TEXT,
          collection_task_id TEXT,
          source_keyword TEXT,
          content_id TEXT,
          comment_id TEXT,
          parent_comment_id TEXT,
          user_id TEXT,
          user_name TEXT,
          comment_text TEXT,
          like_count BIGINT,
          publish_time TEXT,
          publish_datetime TIMESTAMP,
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

        manifest_path = dataset_dir / "dataset.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        except json.JSONDecodeError:
            manifest = {}
        options = manifest.get("options") if isinstance(manifest, dict) else {}
        options = options if isinstance(options, dict) else {}
        collection_task_id = _text(options.get("collection_task_id") or options.get("task_id"))

        # Platform dispatch: detect from manifest, then raw file presence.
        platform = self._resolve_platform(manifest, raw_dir)
        raw_names = RAW_FILE_NAMES.get(platform)
        if raw_names is None:
            raise McpAppError(
                ErrorCode.NORMALIZE_FAILED,
                f"Normalization is not supported for platform {platform!r}",
                f"dataset_id={dataset_id} platforms={manifest.get('platforms') if isinstance(manifest, dict) else None}",
            )
        contents_path = raw_dir / raw_names["contents"]
        comments_path = raw_dir / raw_names["comments"]
        if not contents_path.exists() and not comments_path.exists():
            raise McpAppError(
                ErrorCode.NORMALIZE_FAILED,
                f"No raw {platform} JSONL files found",
                f"Expected {contents_path} or {comments_path}",
            )

        raw_content_items = _read_jsonl(contents_path) if contents_path.exists() else []
        if platform == "douyin":
            content_rows = [_normalize_douyin_content(dataset_id, item, collection_task_id) for item in raw_content_items]
            content_rows = _dedupe_contents(content_rows)
            source_keyword_by_content = {
                row["content_id"]: row["source_keyword"]
                for row in content_rows
                if row["content_id"] and row["source_keyword"]
            }
            raw_comment_items = _read_jsonl(comments_path) if comments_path.exists() else []
            comment_rows = [
                _normalize_douyin_comment(dataset_id, item, source_keyword_by_content, collection_task_id)
                for item in raw_comment_items
            ]
            comment_rows = _dedupe_comments(comment_rows)
        else:
            content_rows = [_normalize_content(dataset_id, item, collection_task_id) for item in raw_content_items]
            content_rows = _dedupe_contents(content_rows)
            source_keyword_by_content = {
                row["content_id"]: row["source_keyword"]
                for row in content_rows
                if row["content_id"] and row["source_keyword"]
            }
            raw_comment_items = _flatten_comment_items(_read_jsonl(comments_path)) if comments_path.exists() else []
            comment_rows = [
                _normalize_comment(dataset_id, item, source_keyword_by_content, collection_task_id)
                for item in raw_comment_items
            ]
            comment_rows = _dedupe_comments(comment_rows)

        orphan_count, lineage_anomaly_count = _normalize_diagnostics(content_rows, comment_rows)

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
            "raw_content_count": len(raw_content_items),
            "raw_comment_count": len(raw_comment_items),
            "deduplicated_content_count": len(raw_content_items) - len(content_rows),
            "deduplicated_comment_count": len(raw_comment_items) - len(comment_rows),
            "orphan_comment_count": orphan_count,
            "reply_lineage_anomaly_count": lineage_anomaly_count,
        }

    @staticmethod
    def _resolve_platform(manifest: dict[str, Any], raw_dir: Path) -> str:
        """Determine the platform for normalization.

        Prefer the manifest's ``platforms`` list.  When absent, probe raw file
        presence so existing XHS datasets without an explicit platform keep
        working.  Unknown platforms are returned as-is so the caller raises a
        clear error instead of silently falling back to XHS.
        """
        platforms = manifest.get("platforms") if isinstance(manifest, dict) else None
        if platforms and isinstance(platforms, list):
            for candidate in platforms:
                if candidate in RAW_FILE_NAMES:
                    return candidate
            first = platforms[0] if platforms else ""
            return _text(first) or "unknown"
        # Manifest-less fallback (utility bundles): probe raw files.
        for platform, names in RAW_FILE_NAMES.items():
            if (raw_dir / names["contents"]).exists() or (raw_dir / names["comments"]).exists():
                return platform
        return "unknown"
