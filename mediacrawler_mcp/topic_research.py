from __future__ import annotations

import html
import json
import re
from collections import Counter
from datetime import datetime, timezone
import hashlib
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import duckdb

from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.utils import strip_sensitive_url_params
from mediacrawler_mcp.utils import make_report_id, utc_now_iso


POSITIVE_MARKERS = ("推荐", "喜欢", "好用", "满意", "值得", "靠谱", "有用", "不错")
NEGATIVE_MARKERS = ("避坑", "不付款", "担心", "最怕", "失望", "问题", "投诉", "太贵", "踩坑")
QUESTION_MARKERS = ("吗", "怎么", "如何", "能不能", "请问", "为什么", "？", "?")
INTENT_MARKERS = ("想买", "购买", "下单", "入手", "求链接", "哪里买", "试试", "想试")
NEGATED_POSITIVE_MARKERS = ("不推荐", "不太好用", "不怎么好用", "不好用", "不值得")
NEGATED_NEGATIVE_MARKERS = ("不是很贵", "不贵", "没那么贵")
TEXT_NORMALIZE_PATTERN = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]+")
INVALID_TOPIC_TAG_PATTERN = re.compile(r"^(?:\d+|\d{1,2}:\d{2}(?::\d{2})?)$")
NEAR_DUPLICATE_THRESHOLD = 0.92
MAX_UNIQUE_TEXTS_CHECKED = 300
REQUIRED_CONTENT_COLUMNS = {
    "collection_task_id",
    "content_type",
    "interaction_field_status",
    "interaction_approximate_fields",
    "interaction_parse_error_fields",
}
REQUIRED_COMMENT_COLUMNS = {"content_id", "comment_id", "parent_comment_id", "comment_text"}
OUTDATED_SCHEMA_MESSAGE = "Dataset schema is outdated. Run normalize_dataset(dataset_id, force=True) before topic research."


def normalize_analysis_text(value: Any) -> str:
    """Deterministic normalisation used only for grouping and diagnostics."""
    return TEXT_NORMALIZE_PATTERN.sub("", str(value or "").lower())


def classify_comment_view(text: str) -> dict[str, Any]:
    """Return independent rule-based comment facets; no category is exclusive."""
    normalized = normalize_analysis_text(text)
    positive_matches = [marker for marker in POSITIVE_MARKERS if marker in normalized]
    negative_matches = [marker for marker in NEGATIVE_MARKERS if marker in normalized]
    negated_positive = [marker for marker in NEGATED_POSITIVE_MARKERS if marker in normalized]
    negated_negative = [marker for marker in NEGATED_NEGATIVE_MARKERS if marker in normalized]
    if negated_positive:
        negative_matches.extend(negated_positive)
        positive_matches = [marker for marker in positive_matches if marker not in {"推荐", "好用", "值得"}]
    if negated_negative:
        negative_matches = [marker for marker in negative_matches if marker not in {"太贵"}]
    has_positive = bool(positive_matches)
    has_negative = bool(negative_matches)
    sentiment = "mixed" if has_positive and has_negative else "positive" if has_positive else "negative" if has_negative else "neutral"
    questions = [marker for marker in QUESTION_MARKERS if marker in str(text or "")]
    intents = [marker for marker in INTENT_MARKERS if marker in normalized]
    return {
        "sentiment": sentiment,
        "is_question": bool(questions),
        "has_action_intent": bool(intents),
        "matched_markers": {
            "positive": positive_matches,
            "negative": negative_matches,
            "question": questions,
            "action_intent": intents,
        },
    }


def duplicate_statistics(records: list[dict[str, Any]], text_key: str) -> dict[str, Any]:
    """Diagnose text duplicates only; normalized storage has already deduped IDs."""
    normalized = [normalize_analysis_text(row.get(text_key, "")) for row in records]
    normalized = [text for text in normalized if text]
    counts = Counter(normalized)
    unique = list(counts)
    checked = unique[:MAX_UNIQUE_TEXTS_CHECKED]
    near_duplicate_pairs = 0
    for index, left in enumerate(checked):
        for right in checked[index + 1 :]:
            if min(len(left), len(right)) >= 8 and SequenceMatcher(None, left, right).ratio() >= NEAR_DUPLICATE_THRESHOLD:
                near_duplicate_pairs += 1
    return {
        "text_exact_duplicate_diagnostic": {
            "input_nonempty_text_count": len(normalized),
            "unique_text_count": len(unique),
            "duplicate_record_count": sum(count - 1 for count in counts.values() if count > 1),
            "action": "diagnostic_only_not_deleted",
        },
        "near_duplicate_diagnostic": {
            "method": "SequenceMatcher",
            "threshold": NEAR_DUPLICATE_THRESHOLD,
            "max_unique_texts_checked": MAX_UNIQUE_TEXTS_CHECKED,
            "unique_texts_checked": len(checked),
            "truncated": len(unique) > len(checked),
            "near_duplicate_pair_count": near_duplicate_pairs,
            "action": "diagnostic_only_not_deleted",
        },
    }


def _first_topic(content: dict[str, Any]) -> str:
    raw_tags = content.get("tags") or "[]"
    try:
        tags = json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
    except json.JSONDecodeError:
        tags = []
    if isinstance(tags, list):
        for tag in tags:
            text = str(tag or "").strip()
            if text and not INVALID_TOPIC_TAG_PATTERN.fullmatch(text):
                return text
    return str(content.get("source_keyword") or "未分类").strip() or "未分类"


def _text_excerpt(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        timestamp = float(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return text


def _safe_url(value: Any) -> str | None:
    url = str(value or "").strip()
    if not url or urlparse(url).scheme.lower() not in {"http", "https"}:
        return None
    return strip_sensitive_url_params(url)


def _is_reply(comment: dict[str, Any]) -> bool:
    return str(comment.get("parent_comment_id") or "").strip() not in {"", "0", "None", "null"}


def _evidence_from_content(content: dict[str, Any], dataset_id: str) -> dict[str, Any]:
    return {
        "content_id": content["content_id"],
        "content_type": content.get("content_type") or "post",
        "parent_content_id": None,
        "parent_comment_id": None,
        "title": content.get("title") or "",
        "text_excerpt": _text_excerpt(content.get("content_text") or content.get("desc") or content.get("title")),
        "author_id": content.get("author_id") or "",
        "published_at": _as_iso(content.get("publish_datetime")),
        "engagement": int(content.get("engagement_count") or 0),
        "source_url": _safe_url(content.get("url")),
        "dataset_id": dataset_id,
        "collection_task_id": content.get("collection_task_id") or "",
    }


def _evidence_from_comment(comment: dict[str, Any], parent: dict[str, Any], dataset_id: str) -> dict[str, Any]:
    return {
        "content_id": comment.get("comment_id") or "",
        "content_type": "reply" if _is_reply(comment) else "comment",
        "parent_content_id": comment.get("content_id") or "",
        "parent_comment_id": comment.get("parent_comment_id") if _is_reply(comment) else None,
        "title": parent.get("title") or "",
        "text_excerpt": _text_excerpt(comment.get("comment_text")),
        "author_id": comment.get("user_id") or "",
        "published_at": _as_iso(comment.get("publish_datetime")),
        "engagement": int(comment.get("like_count") or 0),
        "source_url": _safe_url(parent.get("url")),
        "dataset_id": dataset_id,
        "collection_task_id": comment.get("collection_task_id") or parent.get("collection_task_id") or "",
        "parent_content_excerpt": _text_excerpt(parent.get("content_text") or parent.get("desc") or parent.get("title")),
        "view": classify_comment_view(str(comment.get("comment_text") or "")),
    }


def calculate_evidence_strength(content_count: int, contextual_comment_count: int, missing_url_rate: float) -> dict[str, Any]:
    if content_count >= 3 and contextual_comment_count >= 5 and missing_url_rate == 0:
        level = "high"
    elif content_count >= 1 and (contextual_comment_count >= 1 or missing_url_rate < 0.5):
        level = "medium"
    else:
        level = "low"
    return {
        "level": level,
        "rule": "high: >=3 posts, >=5 contextual comments, and no missing source URLs; medium: >=1 post plus a contextual comment or source URLs on at least half of posts; otherwise low",
    }


def _view_counts(comments: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter()
    for comment in comments:
        view = classify_comment_view(str(comment.get("comment_text") or ""))
        counts[f"{view['sentiment']}_count"] += 1
        if view["is_question"]:
            counts["question_count"] += 1
        if view["has_action_intent"]:
            counts["action_intent_count"] += 1
    return {key: int(counts[key]) for key in ("positive_count", "negative_count", "mixed_count", "neutral_count", "question_count", "action_intent_count")}


def build_topic_analysis(contents: list[dict[str, Any]], comments: list[dict[str, Any]], dataset_id: str, top_n: int = 10) -> dict[str, Any]:
    """Build deterministic tag/source-keyword groups and traceable evidence."""
    by_content_id = {row["content_id"]: row for row in contents if row.get("content_id")}
    groups: dict[str, dict[str, Any]] = {}
    for content in contents:
        name = _first_topic(content)
        key = normalize_analysis_text(name) or name
        groups.setdefault(key, {"name": name, "contents": [], "comments": []})["contents"].append(content)
    orphan_comments: list[dict[str, Any]] = []
    for comment in comments:
        parent = by_content_id.get(comment.get("content_id"))
        if parent is None:
            orphan_comments.append(comment)
            continue
        name = _first_topic(parent)
        key = normalize_analysis_text(name) or name
        groups.setdefault(key, {"name": name, "contents": [], "comments": []})["comments"].append(comment)

    topics = []
    for group in groups.values():
        topic_contents = group["contents"]
        topic_comments = group["comments"]
        representative_posts = [
            _evidence_from_content(row, dataset_id)
            for row in sorted(topic_contents, key=lambda row: int(row.get("engagement_count") or 0), reverse=True)[:3]
        ]
        representative_comments = [
            _evidence_from_comment(row, by_content_id[row["content_id"]], dataset_id)
            for row in sorted(topic_comments, key=lambda row: int(row.get("like_count") or 0), reverse=True)[:3]
        ]
        classified_comments = [
            _evidence_from_comment(row, by_content_id[row["content_id"]], dataset_id)
            for row in sorted(topic_comments, key=lambda row: int(row.get("like_count") or 0), reverse=True)
        ]
        positive = next((item for item in classified_comments if item["view"]["sentiment"] == "positive"), None)
        negative = next((item for item in classified_comments if item["view"]["sentiment"] == "negative"), None)
        contrasting = {"positive_example": positive, "negative_example": negative} if positive and negative else None
        authors = {str(row.get("author_id") or "") for row in topic_contents} | {str(row.get("user_id") or "") for row in topic_comments}
        authors.discard("")
        total_interaction = sum(int(row.get("engagement_count") or 0) for row in topic_contents) + sum(int(row.get("like_count") or 0) for row in topic_comments)
        dates = Counter(_as_iso(row.get("publish_datetime"))[:10] if _as_iso(row.get("publish_datetime")) else "unknown" for row in [*topic_contents, *topic_comments])
        topics.append({
            "name": group["name"],
            "grouping_method": "tag_then_source_keyword_deterministic_grouping",
            "grouping_limitations": ["No semantic clustering is used.", "Different subtopics under the same search keyword can be merged.", "Grouping quality depends on source tags and search keywords."],
            "description": f"标签/搜索关键词确定性分组：{len(topic_contents)} 篇帖子及其关联评论。",
            "post_count": len(topic_contents),
            "comment_count": len(topic_comments),
            "unique_author_count": len(authors),
            "total_engagement": total_interaction,
            "average_engagement": round(total_interaction / max(1, len(topic_contents) + len(topic_comments)), 2),
            "time_distribution": dict(sorted(dates.items())),
            "representative_posts": representative_posts,
            "representative_comments": representative_comments,
            "view_counts": _view_counts(topic_comments),
            "contrasting_view_examples": contrasting,
            "contrasting_view_limitations": "Positive and negative examples are selected independently; they are not strict counterexamples about the same attribute.",
            "evidence": [*representative_posts, *representative_comments],
        })
    topics.sort(key=lambda row: (-row["total_engagement"], row["name"]))
    return {"topics": topics[:top_n], "orphan_comment_count": len(orphan_comments)}


class TopicResearchService:
    """A fixed, rules-based topic grouping and evidence report MVP."""

    def __init__(self, storage: Storage):
        self.storage = storage

    def generate_topic_research_report(self, dataset_id: str, top_n: int = 10) -> dict[str, Any]:
        dataset_id = (dataset_id or "").strip()
        top_n = max(1, min(int(top_n or 10), 50))
        if not dataset_id:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Dataset ID is required")
        self.storage.initialize()
        row = self.storage.get_dataset_row(dataset_id)
        if row is None:
            raise McpAppError(ErrorCode.DATASET_NOT_FOUND, "Dataset not found", f"dataset_id={dataset_id}")
        dataset_dir = Path(row["dataset_dir"])
        database_path = dataset_dir / "analysis.duckdb"
        if not database_path.exists():
            raise McpAppError(ErrorCode.REPORT_FAILED, "Dataset is not normalized", "Call normalize_dataset before topic research")
        manifest = self._read_manifest(dataset_dir / "dataset.json")
        with duckdb.connect(str(database_path), read_only=True) as conn:
            self._validate_schema(conn)
            contents = self._rows(conn, "SELECT * FROM contents")
            comments = self._rows(conn, "SELECT * FROM comments")
        summary = {
            "report_id": make_report_id("topic_research"),
            "report_type": "topic_research",
            "report_capability": "可追溯的规则型主题分组与证据报告 MVP",
            "dataset": {"dataset_id": dataset_id, "name": row["name"], "platforms": json.loads(row["platforms_json"])},
            "scope": self._scope(row, manifest, contents, comments),
            "data_quality": self._quality(dataset_dir, manifest, contents, comments),
            "topic_analysis": build_topic_analysis(contents, comments, dataset_id, top_n),
            "limitations": [
                "This is deterministic tag/source-keyword grouping, not semantic topic clustering.",
                "This report is a single dataset snapshot and does not claim a topic is rising, falling, or trending without historical snapshots or a baseline.",
                "No LLM generated or altered counts, samples, or conclusions.",
            ],
        }
        report_dir = dataset_dir / "reports"
        report_dir.mkdir(exist_ok=True)
        summary_path = report_dir / "topic_research.summary.json"
        markdown_path = report_dir / "topic_research.md"
        html_path = report_dir / "topic_research.html"
        summary["outputs"] = {"summary_json_path": str(summary_path), "report_md_path": str(markdown_path), "report_html_path": str(html_path)}
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_markdown(markdown_path, summary)
        self._write_html(html_path, summary)
        now = utc_now_iso()
        self.storage.upsert_report(summary["report_id"], dataset_id, "topic_research", "success", str(markdown_path), str(html_path), str(summary_path), now, now)
        return {"report_id": summary["report_id"], **summary["outputs"], "summary": summary}

    @staticmethod
    def _rows(conn: duckdb.DuckDBPyConnection, query: str) -> list[dict[str, Any]]:
        cursor = conn.execute(query)
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, values)) for values in cursor.fetchall()]

    @staticmethod
    def _validate_schema(conn: duckdb.DuckDBPyConnection) -> None:
        def columns(table: str) -> set[str]:
            try:
                return {str(row[1]) for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
            except duckdb.Error:
                return set()

        if REQUIRED_CONTENT_COLUMNS - columns("contents") or REQUIRED_COMMENT_COLUMNS - columns("comments"):
            raise McpAppError(ErrorCode.REPORT_FAILED, OUTDATED_SCHEMA_MESSAGE)

    @staticmethod
    def _read_manifest(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except json.JSONDecodeError:
            value = {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _raw_count(path: Path) -> int:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()) if path.exists() else 0

    def _quality(self, dataset_dir: Path, manifest: dict[str, Any], contents: list[dict[str, Any]], comments: list[dict[str, Any]]) -> dict[str, Any]:
        raw_content_count = self._raw_count(dataset_dir / "raw" / "xhs_contents.jsonl")
        raw_comment_count = self._raw_count(dataset_dir / "raw" / "xhs_comments.jsonl")
        content_ids = {row.get("content_id") for row in contents}
        def record_key(kind: str, row: dict[str, Any], index: int) -> str:
            identifier = row.get("content_id") if kind == "content" else row.get("comment_id")
            if str(identifier or "").strip():
                return f"{kind}:{identifier}"
            raw = json.dumps(row.get("raw_json") or row, ensure_ascii=False, sort_keys=True, default=str)
            return f"{kind}:missing:{index}:{hashlib.sha256(raw.encode()).hexdigest()[:12]}"

        missing_url_ids = {record_key("content", row, index) for index, row in enumerate(contents) if not _safe_url(row.get("url"))}
        interaction_missing_ids = {record_key("content", row, index) for index, row in enumerate(contents) if row.get("interaction_field_status") == "missing"}
        interaction_parse_error_ids = {record_key("content", row, index) for index, row in enumerate(contents) if row.get("interaction_field_status") in {"parse_error", "partial_parse_error"}}
        interaction_zero_ids = {record_key("content", row, index) for index, row in enumerate(contents) if row.get("interaction_field_status") == "present_zero"}
        interaction_approximate_ids = {record_key("content", row, index) for index, row in enumerate(contents) if row.get("interaction_field_status") == "present_approximate"}
        orphan_ids = {record_key("comment", row, index) for index, row in enumerate(comments) if row.get("content_id") not in content_ids}
        anomaly_ids = missing_url_ids | interaction_missing_ids | interaction_parse_error_ids | orphan_ids
        missing_rates = {
            "content_source_url": len(missing_url_ids) / len(contents) if contents else None,
            "content_interaction_fields": len(interaction_missing_ids) / len(contents) if contents else None,
            "comment_parent_context": len(orphan_ids) / len(comments) if comments else None,
        }
        options = manifest.get("options") if isinstance(manifest.get("options"), dict) else {}
        unavailable = [name for name in ("discovered_count", "requested_count", "successful_parse_count") if options.get(name) is None]
        return {
            "pipeline_counts": {"discovered_count": options.get("discovered_count"), "requested_count": options.get("requested_count"), "successful_parse_count": options.get("successful_parse_count"), "raw_content_count": raw_content_count, "raw_comment_count": raw_comment_count, "normalized_content_count": len(contents), "normalized_comment_count": len(comments)},
            "missing_rates": missing_rates,
            "interaction_field_status_counts": {"present": sum(row.get("interaction_field_status") == "present" for row in contents), "present_zero": len(interaction_zero_ids), "missing": len(interaction_missing_ids), "parse_error": sum(row.get("interaction_field_status") == "parse_error" for row in contents), "partial_parse_error": sum(row.get("interaction_field_status") == "partial_parse_error" for row in contents), "present_approximate": len(interaction_approximate_ids)},
            "anomaly_sample_count": len(anomaly_ids),
            "anomaly_type_counts": {"missing_source_url": len(missing_url_ids), "missing_interaction_fields": len(interaction_missing_ids), "interaction_parse_error": len(interaction_parse_error_ids), "orphan_comment": len(orphan_ids)},
            "interaction_approximate_fields": {row.get("content_id") or record_key("content", row, index): json.loads(row.get("interaction_approximate_fields") or "[]") for index, row in enumerate(contents) if row.get("interaction_approximate_fields") not in {None, "", "[]"}},
            "interaction_parse_error_fields": {row.get("content_id") or record_key("content", row, index): json.loads(row.get("interaction_parse_error_fields") or "[]") for index, row in enumerate(contents) if row.get("interaction_parse_error_fields") not in {None, "", "[]"}},
            "deduplication": {"normalization_id_deduplication": "content and comment rows are deduplicated by their IDs during normalization", "content_text": duplicate_statistics(contents, "content_text"), "comment_text": duplicate_statistics(comments, "comment_text")},
            "unavailable_metrics": unavailable,
            "evidence_strength": calculate_evidence_strength(len(contents), len(comments) - len(orphan_ids), 1.0 if missing_rates["content_source_url"] is None else missing_rates["content_source_url"]),
        }

    @staticmethod
    def _scope(row: dict[str, Any], manifest: dict[str, Any], contents: list[dict[str, Any]], comments: list[dict[str, Any]]) -> dict[str, Any]:
        options = manifest.get("options") if isinstance(manifest.get("options"), dict) else {}
        timestamps = sorted(value for value in (_as_iso(item.get("publish_datetime")) for item in [*contents, *comments]) if value)
        crawled = sorted(value for value in (_as_iso(item.get("crawl_time")) for item in [*contents, *comments]) if value)
        replies = sum(_is_reply(item) for item in comments)
        authors = {item.get("author_id") for item in contents if item.get("author_id")} | {item.get("user_id") for item in comments if item.get("user_id")}
        declared = json.loads(row["keywords_json"])
        observed = sorted({str(item.get("source_keyword") or "").strip() for item in [*contents, *comments] if str(item.get("source_keyword") or "").strip()})
        return {"platforms": json.loads(row["platforms_json"]), "dataset_id": row["dataset_id"], "data_time_range": {"from": timestamps[0] if timestamps else None, "to": timestamps[-1] if timestamps else None}, "collection_time_range": {"from": _as_iso(options.get("collection_started_at")) or (crawled[0] if crawled else None), "to": _as_iso(options.get("collection_completed_at")) or (crawled[-1] if crawled else None)}, "post_count": len(contents), "comment_count": len(comments) - replies, "reply_count": replies, "unique_author_count": len(authors), "source_keywords": declared, "declared_source_keywords": declared, "observed_source_keywords": observed, "crawler_type": options.get("crawler_type") or "unavailable", "search_sort": options.get("search_sort") or "unavailable", "collection_task_id": options.get("collection_task_id") or "unavailable"}

    @staticmethod
    def _markdown_evidence(item: dict[str, Any]) -> str:
        label = f"[{item['content_type']}] {item['title'] or item['content_id']}"
        url = _safe_url(item.get("source_url"))
        link = f"[{label}]({url})" if url else label
        parent = f"；父帖={item['parent_content_id']}" if item.get("parent_content_id") else ""
        reply = f"；父评论={item['parent_comment_id']}" if item.get("parent_comment_id") else ""
        return f"- {link}{parent}{reply}：{item['text_excerpt']}"

    @classmethod
    def _write_markdown(cls, path: Path, summary: dict[str, Any]) -> None:
        scope, quality = summary["scope"], summary["data_quality"]
        content_dedup = quality["deduplication"]["content_text"]
        comment_dedup = quality["deduplication"]["comment_text"]
        lines = [f"# {summary['dataset']['name']}：规则型主题分组与证据报告", "", "## 数据范围", "", f"- 数据集：`{scope['dataset_id']}`；平台：{', '.join(scope['platforms'])}", f"- 采集任务：{scope['collection_task_id']}；采集时间：{scope['collection_time_range']['from']} 至 {scope['collection_time_range']['to']}", f"- 数据时间：{scope['data_time_range']['from']} 至 {scope['data_time_range']['to']}", f"- 帖子 / 评论 / 回复：{scope['post_count']} / {scope['comment_count']} / {scope['reply_count']}", f"- 声明关键词：{', '.join(scope['declared_source_keywords']) or '无'}；实际 observed 关键词：{', '.join(scope['observed_source_keywords']) or '无'}", "", "## 数据质量", "", f"- 证据强度：**{quality['evidence_strength']['level']}**", f"- 异常样本（按唯一记录计）：{quality['anomaly_sample_count']}；按类型：{json.dumps(quality['anomaly_type_counts'], ensure_ascii=False)}", f"- 互动字段：{json.dumps(quality['interaction_field_status_counts'], ensure_ascii=False)}", "- 去重：归一化阶段按 ID 去重；文本重复仅诊断、不删除。", f"  - 内容精确重复记录：{content_dedup['text_exact_duplicate_diagnostic']['duplicate_record_count']}；近似重复对：{content_dedup['near_duplicate_diagnostic']['near_duplicate_pair_count']}（{content_dedup['near_duplicate_diagnostic']['method']}，阈值 {content_dedup['near_duplicate_diagnostic']['threshold']}，检查 {content_dedup['near_duplicate_diagnostic']['unique_texts_checked']} 条唯一文本）", f"  - 评论精确重复记录：{comment_dedup['text_exact_duplicate_diagnostic']['duplicate_record_count']}；近似重复对：{comment_dedup['near_duplicate_diagnostic']['near_duplicate_pair_count']}", f"- 不可获得指标：{', '.join(quality['unavailable_metrics']) or '无'}", "", "## 分组方法与限制", "", "- 方法：标签优先、搜索关键词降级的确定性分组；不使用语义聚类。", "- 限制：同一搜索关键词的子议题可能合并；标签质量会影响结果。", "", "## 主题", ""]
        for topic in summary["topic_analysis"]["topics"]:
            lines.extend([f"### {topic['name']}", f"- 帖子：{topic['post_count']}；评论：{topic['comment_count']}；作者：{topic['unique_author_count']}；总互动：{topic['total_engagement']}；平均互动：{topic['average_engagement']}", f"- 观点统计：{json.dumps(topic['view_counts'], ensure_ascii=False)}", "- 代表帖子：", *[cls._markdown_evidence(item) for item in topic["representative_posts"]], "- 代表评论：", *[cls._markdown_evidence(item) for item in topic["representative_comments"]]])
            contrasting = topic["contrasting_view_examples"]
            if contrasting:
                lines.extend(["- 正负面示例（非同一属性上的严格反例）：", cls._markdown_evidence(contrasting["positive_example"]), cls._markdown_evidence(contrasting["negative_example"])])
        lines.extend(["", "## 限制", "", *[f"- {item}" for item in summary["limitations"]]])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _html_evidence(item: dict[str, Any]) -> str:
        label = html.escape(f"[{item['content_type']}] {item['title'] or item['content_id']}")
        url = _safe_url(item.get("source_url"))
        link = f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{label}</a>' if url else label
        parent = f"<span> parent post: {html.escape(str(item['parent_content_id']))}</span>" if item.get("parent_content_id") else ""
        reply = f"<span> parent comment: {html.escape(str(item['parent_comment_id']))}</span>" if item.get("parent_comment_id") else ""
        return f"<li>{link}{parent}{reply} — {html.escape(item['text_excerpt'])}</li>"

    @classmethod
    def _write_html(cls, path: Path, summary: dict[str, Any]) -> None:
        scope, quality = summary["scope"], summary["data_quality"]
        sections = []
        for topic in summary["topic_analysis"]["topics"]:
            posts = "".join(cls._html_evidence(item) for item in topic["representative_posts"])
            comments = "".join(cls._html_evidence(item) for item in topic["representative_comments"])
            contrasting = topic["contrasting_view_examples"]
            contrasts = "" if not contrasting else f"<h3>Positive/negative examples (not attribute-aligned counterexamples)</h3><ul>{cls._html_evidence(contrasting['positive_example'])}{cls._html_evidence(contrasting['negative_example'])}</ul>"
            sections.append(f"<section><h2>{html.escape(topic['name'])}</h2><p>Posts {topic['post_count']}; comments {topic['comment_count']}; total engagement {topic['total_engagement']}</p><p>View counts: {html.escape(json.dumps(topic['view_counts'], ensure_ascii=False))}</p><h3>Representative posts</h3><ul>{posts}</ul><h3>Representative comments</h3><ul>{comments}</ul>{contrasts}</section>")
        unavailable = ", ".join(quality["unavailable_metrics"]) or "none"
        content_dedup = quality["deduplication"]["content_text"]
        comment_dedup = quality["deduplication"]["comment_text"]
        observed = ", ".join(scope["observed_source_keywords"]) or "none"
        path.write_text(f"<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>{html.escape(summary['dataset']['name'])} topic research</title><body><h1>{html.escape(summary['dataset']['name'])}：规则型主题分组与证据报告</h1><section><h2>Data scope</h2><p>Task: {html.escape(str(scope['collection_task_id']))}; collection: {html.escape(str(scope['collection_time_range']))}</p><p>Posts/comments/replies: {scope['post_count']}/{scope['comment_count']}/{scope['reply_count']}</p><p>Observed source keywords: {html.escape(observed)}</p></section><section><h2>Data quality</h2><p>Unique anomalous records: {quality['anomaly_sample_count']}</p><p>Unavailable metrics: {html.escape(unavailable)}</p><p>Deduplication: ID dedupe during normalization; text duplicate diagnostics do not delete records. Content exact duplicates: {content_dedup['text_exact_duplicate_diagnostic']['duplicate_record_count']}; near pairs: {content_dedup['near_duplicate_diagnostic']['near_duplicate_pair_count']}. Comment exact duplicates: {comment_dedup['text_exact_duplicate_diagnostic']['duplicate_record_count']}; near pairs: {comment_dedup['near_duplicate_diagnostic']['near_duplicate_pair_count']}.</p></section><section><h2>Grouping method and limitations</h2><p>Deterministic tag/source-keyword grouping, not semantic clustering. Search-keyword subtopics can be merged and tag quality affects results.</p></section>{''.join(sections)}<section><h2>Limitations</h2><ul>{''.join(f'<li>{html.escape(item)}</li>' for item in summary['limitations'])}</ul></section></body></html>", encoding="utf-8")
