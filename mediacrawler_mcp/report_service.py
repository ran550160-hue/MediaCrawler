from __future__ import annotations

import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import duckdb

try:  # jieba is optional in some lightweight MCP deployments.
    import jieba
except Exception:  # pragma: no cover - import-time optional dependency guard
    jieba = None

from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.utils import make_report_id, utc_now_iso


AD_KEYWORDS = ("私信", "进群", "课程", "训练营", "资料包", "领取", "加我", "变现")
COMMENT_STOPWORDS = {
    "这个",
    "那个",
    "就是",
    "还是",
    "因为",
    "所以",
    "真的",
    "没有",
    "一个",
    "可以",
    "应该",
}
TOKEN_PATTERN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _keyword_distribution(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT source_keyword, COUNT(*) AS count
        FROM contents
        WHERE source_keyword != ''
        GROUP BY source_keyword
        ORDER BY count DESC, source_keyword ASC
        """
    ).fetchall()
    return {str(keyword): int(count) for keyword, count in rows}


def _order_keyword_distribution(distribution: dict[str, int], dataset_keywords: list[str]) -> dict[str, int]:
    ordered = {keyword: distribution[keyword] for keyword in dataset_keywords if keyword in distribution}
    for keyword, count in distribution.items():
        if keyword not in ordered:
            ordered[keyword] = count
    return ordered


def _parse_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _top_contents(conn: duckdb.DuckDBPyConnection, top_n: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT content_id, title, author_name, url, source_keyword, tags,
               CAST(publish_datetime AS VARCHAR) AS publish_datetime,
               like_count, collect_count, comment_count, share_count, engagement_count
        FROM contents
        ORDER BY engagement_count DESC, content_id ASC
        LIMIT ?
        """,
        [top_n],
    ).fetchall()
    columns = (
        "content_id",
        "title",
        "author_name",
        "url",
        "source_keyword",
        "tags",
        "publish_datetime",
        "like_count",
        "collect_count",
        "comment_count",
        "share_count",
        "engagement_count",
    )
    results = [dict(zip(columns, row)) for row in rows]
    for result in results:
        result["tags"] = _parse_tags(result.get("tags"))
    return results


def _top_comments(conn: duckdb.DuckDBPyConnection, top_n: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT comment_id, content_id, user_name, comment_text, source_keyword, like_count
        FROM comments
        ORDER BY like_count DESC, comment_id ASC
        LIMIT ?
        """,
        [top_n],
    ).fetchall()
    columns = ("comment_id", "content_id", "user_name", "comment_text", "source_keyword", "like_count")
    return [dict(zip(columns, row)) for row in rows]


def _fallback_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for part in TOKEN_PATTERN.findall(text):
        if CHINESE_PATTERN.search(part) and len(part) > 4:
            tokens.extend(part[index : index + 2] for index in range(0, len(part), 2))
        else:
            tokens.append(part)
    return tokens


def _comment_tokens(text: str) -> list[str]:
    raw_tokens = jieba.lcut(text) if jieba else _fallback_tokens(text)
    tokens: list[str] = []
    for raw_token in raw_tokens:
        matches = TOKEN_PATTERN.findall(str(raw_token).strip().lower())
        token = "".join(matches)
        if len(token) < 2:
            continue
        if token.isdigit() or token in COMMENT_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _word_frequency(conn: duckdb.DuckDBPyConnection, top_n: int = 50) -> dict[str, int]:
    rows = conn.execute("SELECT comment_text FROM comments WHERE comment_text != ''").fetchall()
    counter: Counter[str] = Counter()
    for (text,) in rows:
        for token in _comment_tokens(str(text)):
            counter[token] += 1
    return {word: int(count) for word, count in counter.most_common(top_n)}


def _keyword_stopwords(values: list[str]) -> set[str]:
    stopwords: set[str] = set()
    for value in values:
        text = str(value or "").strip().lower()
        if not text:
            continue
        stopwords.add(text)
        stopwords.update(_comment_tokens(text))
    return stopwords


def _content_word_frequency(
    conn: duckdb.DuckDBPyConnection,
    dataset_keywords: list[str],
    top_n: int = 50,
) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT title, "desc", content_text, tags, source_keyword
        FROM contents
        """
    ).fetchall()
    source_keywords = [str(row[4] or "") for row in rows]
    excluded = COMMENT_STOPWORDS | _keyword_stopwords(dataset_keywords + source_keywords)
    counter: Counter[str] = Counter()
    for title, desc, content_text, tags, _source_keyword in rows:
        tag_text = " ".join(_parse_tags(tags))
        text = " ".join(str(value or "") for value in (title, desc, content_text, tag_text))
        for token in _comment_tokens(text):
            if token in excluded:
                continue
            counter[token] += 1
    return {word: int(count) for word, count in counter.most_common(top_n)}


def _interaction_warnings(conn: duckdb.DuckDBPyConnection, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT content_id, title, source_keyword, like_count, collect_count,
               comment_count, share_count, engagement_count
        FROM contents
        WHERE engagement_count >= 10000
          AND (like_count = 0 OR collect_count = 0)
        ORDER BY engagement_count DESC, content_id ASC
        LIMIT ?
        """,
        [limit],
    ).fetchall()
    columns = (
        "content_id",
        "title",
        "source_keyword",
        "like_count",
        "collect_count",
        "comment_count",
        "share_count",
        "engagement_count",
    )
    warnings = []
    for row in rows:
        warning = dict(zip(columns, row))
        missing = []
        if int(warning["like_count"] or 0) == 0:
            missing.append("like_count")
        if int(warning["collect_count"] or 0) == 0:
            missing.append("collect_count")
        warning["warning"] = f"High engagement with zero {', '.join(missing)}"
        warnings.append(warning)
    return warnings


def _ad_candidates(conn: duckdb.DuckDBPyConnection, limit: int = 20) -> list[dict[str, Any]]:
    clauses = " OR ".join(["concat_ws(' ', title, \"desc\", content_text) LIKE ?" for _ in AD_KEYWORDS])
    params = [f"%{keyword}%" for keyword in AD_KEYWORDS]
    rows = conn.execute(
        f"""
        SELECT content_id, title, url, source_keyword, engagement_count
        FROM contents
        WHERE {clauses}
        ORDER BY engagement_count DESC
        LIMIT ?
        """,
        params + [limit],
    ).fetchall()
    columns = ("content_id", "title", "url", "source_keyword", "engagement_count")
    return [dict(zip(columns, row)) for row in rows]


def _markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "暂无数据\n"
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = [
        "| " + " | ".join(str(row.get(column, "")).replace("\n", " ") for column in columns) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *body]) + "\n"


def _write_markdown(path: Path, summary: dict[str, Any]) -> None:
    metrics = summary["metrics"]
    content_keywords = summary.get("content_word_freq", {})
    lines = [
        f"# {summary['dataset']['name']} 报告",
        "",
        "## 数据概览",
        "",
        f"- 数据集 ID：`{summary['dataset']['dataset_id']}`",
        f"- 平台：{', '.join(summary['dataset']['platforms'])}",
        f"- 关键词：{', '.join(summary['dataset']['keywords'])}",
        f"- 内容数：{metrics['content_count']}",
        f"- 评论数：{metrics['comment_count']}",
        f"- 作者数：{metrics['author_count']}",
        f"- 评论用户数：{metrics['comment_user_count']}",
        "",
        "## 关键词分布",
        "",
        *[f"- {key}: {value}" for key, value in summary["keyword_distribution"].items()],
        "",
        "## 内容高频词",
        "",
        *[f"- {key}: {value}" for key, value in list(content_keywords.items())[:20]],
        "",
        "## 高互动内容",
        "",
        _markdown_table(summary["top_contents"], ["content_id", "title", "source_keyword", "publish_datetime", "engagement_count", "url"]),
        "",
        "## 高赞评论",
        "",
        _markdown_table(summary["top_comments"], ["comment_id", "content_id", "comment_text", "source_keyword", "like_count"]),
        "",
        "## 疑似广告或引流候选",
        "",
        _markdown_table(summary["ad_candidates"], ["content_id", "title", "source_keyword", "engagement_count", "url"]),
        "",
        "## 数据质量提示",
        "",
        _markdown_table(summary.get("interaction_warnings", []), ["content_id", "title", "source_keyword", "like_count", "collect_count", "engagement_count", "warning"]),
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_html(path: Path, summary: dict[str, Any]) -> None:
    metrics = summary["metrics"]
    keyword_items = "".join(
        f"<li>{_escape(key)}: {int(value)}</li>"
        for key, value in summary["keyword_distribution"].items()
    ) or "<li>暂无数据</li>"
    content_keyword_items = "".join(
        f"<li>{_escape(key)}: {int(value)}</li>"
        for key, value in list(summary.get("content_word_freq", {}).items())[:20]
    ) or "<li>暂无数据</li>"

    def table(rows: list[dict[str, Any]], columns: list[str]) -> str:
        if not rows:
            return "<p>暂无数据</p>"
        head = "".join(f"<th>{_escape(column)}</th>" for column in columns)
        body = "".join(
            "<tr>" + "".join(f"<td>{_escape(row.get(column, ''))}</td>" for column in columns) + "</tr>"
            for row in rows
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    path.write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{_escape(summary['dataset']['name'])} 报告</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #202124; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0 24px; }}
    th, td {{ border: 1px solid #dadce0; padding: 8px; text-align: left; }}
    th {{ background: #f8fafd; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }}
    .metric {{ border: 1px solid #dadce0; padding: 12px; }}
    .metric strong {{ display: block; font-size: 24px; }}
  </style>
</head>
<body>
  <h1>{_escape(summary['dataset']['name'])} 报告</h1>
  <section class="metrics">
    <div class="metric">内容数<strong>{metrics['content_count']}</strong></div>
    <div class="metric">评论数<strong>{metrics['comment_count']}</strong></div>
    <div class="metric">作者数<strong>{metrics['author_count']}</strong></div>
    <div class="metric">评论用户数<strong>{metrics['comment_user_count']}</strong></div>
  </section>
  <h2>关键词分布</h2>
  <ul>{keyword_items}</ul>
  <h2>内容高频词</h2>
  <ul>{content_keyword_items}</ul>
  <h2>高互动内容</h2>
  {table(summary['top_contents'], ['content_id', 'title', 'source_keyword', 'publish_datetime', 'engagement_count', 'url'])}
  <h2>高赞评论</h2>
  {table(summary['top_comments'], ['comment_id', 'content_id', 'comment_text', 'source_keyword', 'like_count'])}
  <h2>疑似广告或引流候选</h2>
  {table(summary['ad_candidates'], ['content_id', 'title', 'source_keyword', 'engagement_count', 'url'])}
  <h2>数据质量提示</h2>
  {table(summary.get('interaction_warnings', []), ['content_id', 'title', 'source_keyword', 'like_count', 'collect_count', 'engagement_count', 'warning'])}
</body>
</html>
""",
        encoding="utf-8",
    )


class ReportService:
    def __init__(self, storage: Storage):
        self.storage = storage

    def generate_report(self, dataset_id: str, report_type: str = "generic", top_n: int = 20) -> dict[str, Any]:
        dataset_id = (dataset_id or "").strip()
        report_type = (report_type or "generic").strip()
        top_n = max(1, min(int(top_n or 20), 100))
        if not dataset_id:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Dataset ID is required")

        self.storage.initialize()
        row = self.storage.get_dataset_row(dataset_id)
        if row is None:
            raise McpAppError(ErrorCode.DATASET_NOT_FOUND, "Dataset not found", f"dataset_id={dataset_id}")

        dataset_dir = Path(row["dataset_dir"])
        duckdb_path = dataset_dir / "analysis.duckdb"
        if not duckdb_path.exists():
            raise McpAppError(ErrorCode.REPORT_FAILED, "Dataset is not normalized", "Call normalize_dataset before generate_report")

        reports_dir = dataset_dir / "reports"
        reports_dir.mkdir(exist_ok=True)
        report_id = make_report_id(report_type)
        report_md_path = reports_dir / "report.md"
        report_html_path = reports_dir / "report.html"
        summary_json_path = reports_dir / "summary.json"

        with duckdb.connect(str(duckdb_path), read_only=True) as conn:
            dataset_keywords = json.loads(row["keywords_json"])
            metrics = {
                "content_count": int(conn.execute("SELECT COUNT(*) FROM contents").fetchone()[0]),
                "comment_count": int(conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]),
                "author_count": int(conn.execute("SELECT COUNT(DISTINCT author_id) FROM contents WHERE author_id != ''").fetchone()[0]),
                "comment_user_count": int(conn.execute("SELECT COUNT(DISTINCT user_id) FROM comments WHERE user_id != ''").fetchone()[0]),
            }
            summary = {
                "report_id": report_id,
                "report_type": report_type,
                "dataset": {
                    "dataset_id": row["dataset_id"],
                    "name": row["name"],
                    "platforms": json.loads(row["platforms_json"]),
                    "keywords": dataset_keywords,
                },
                "metrics": metrics,
                "keyword_distribution": _order_keyword_distribution(_keyword_distribution(conn), dataset_keywords),
                "top_contents": _top_contents(conn, top_n),
                "top_comments": _top_comments(conn, top_n),
                "content_word_freq": _content_word_frequency(conn, dataset_keywords),
                "comment_word_freq": _word_frequency(conn),
                "interaction_warnings": _interaction_warnings(conn),
                "ad_candidates": _ad_candidates(conn),
                "outputs": {
                    "summary_json_path": str(summary_json_path),
                    "report_md_path": str(report_md_path),
                    "report_html_path": str(report_html_path),
                },
            }

        _write_json(summary_json_path, summary)
        _write_markdown(report_md_path, summary)
        _write_html(report_html_path, summary)
        now = utc_now_iso()
        self.storage.upsert_report(
            report_id=report_id,
            dataset_id=dataset_id,
            report_type=report_type,
            status="success",
            report_md_path=str(report_md_path),
            report_html_path=str(report_html_path),
            summary_json_path=str(summary_json_path),
            created_at=now,
            updated_at=now,
        )
        return {
            "report_id": report_id,
            "report_md_path": str(report_md_path),
            "report_html_path": str(report_html_path),
            "summary_json_path": str(summary_json_path),
            "summary": {
                "content_count": metrics["content_count"],
                "comment_count": metrics["comment_count"],
                "top_keywords": list(summary["content_word_freq"].keys())[:10],
                "high_engagement_posts": summary["top_contents"][:5],
                "interaction_warnings": summary["interaction_warnings"][:5],
            },
        }

    def get_report(self, dataset_id: str, report_id: str | None = None) -> dict[str, Any]:
        dataset_id = (dataset_id or "").strip()
        report_id = report_id.strip() if report_id else None
        if not dataset_id:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Dataset ID is required")

        self.storage.initialize()
        if self.storage.get_dataset_row(dataset_id) is None:
            raise McpAppError(ErrorCode.DATASET_NOT_FOUND, "Dataset not found", f"dataset_id={dataset_id}")

        row = self.storage.get_report_row(dataset_id, report_id)
        if row is None:
            raise McpAppError(ErrorCode.REPORT_NOT_FOUND, "Report not found", f"dataset_id={dataset_id}")
        return {
            "report_id": row["report_id"],
            "dataset_id": row["dataset_id"],
            "report_type": row["report_type"],
            "status": row["status"],
            "report_md_path": row["report_md_path"],
            "report_html_path": row["report_html_path"],
            "summary_json_path": row["summary_json_path"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
