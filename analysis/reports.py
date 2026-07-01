from __future__ import annotations

import html
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import jieba
import matplotlib
import pandas as pd
from wordcloud import WordCloud

import config

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


class AnalysisReportError(ValueError):
    """Raised when an analysis report cannot be generated."""


@dataclass(frozen=True)
class PlatformFields:
    content_id: str
    content_url: str


PLATFORM_FIELDS = {
    "xhs": PlatformFields(content_id="note_id", content_url="note_url"),
    "dy": PlatformFields(content_id="aweme_id", content_url="aweme_url"),
}

NUMERIC_CONTENT_FIELDS = (
    "liked_count",
    "collected_count",
    "comment_count",
    "share_count",
)


def _platform_fields(platform: str) -> PlatformFields:
    try:
        return PLATFORM_FIELDS[platform]
    except KeyError as exc:
        supported = ", ".join(sorted(PLATFORM_FIELDS))
        raise AnalysisReportError(f"Unsupported platform: {platform}. Supported: {supported}") from exc


def _numeric_column(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series([0] * len(df), index=df.index, dtype="int64")
    cleaned = df[column].astype(str).str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(cleaned, errors="coerce").fillna(0).astype("int64")


def _text_column(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series([""] * len(df), index=df.index, dtype="object")
    return df[column].fillna("").astype(str)


def normalize_contents(contents_df: Optional[pd.DataFrame], platform: str) -> pd.DataFrame:
    fields = _platform_fields(platform)
    if contents_df is None:
        contents_df = pd.DataFrame()
    contents = contents_df.copy()
    if contents.empty:
        return contents

    contents["_content_id"] = _text_column(contents, fields.content_id)
    contents["_url"] = _text_column(contents, fields.content_url)
    for field in NUMERIC_CONTENT_FIELDS:
        contents[field] = _numeric_column(contents, field)
    contents["engagement_count"] = contents[list(NUMERIC_CONTENT_FIELDS)].sum(axis=1)
    contents = _dedupe_by_id(contents, "_content_id", "engagement_count")
    return contents


def normalize_comments(comments_df: Optional[pd.DataFrame], platform: str) -> pd.DataFrame:
    fields = _platform_fields(platform)
    if comments_df is None:
        comments_df = pd.DataFrame()
    comments = comments_df.copy()
    if comments.empty:
        return comments

    comments["_content_id"] = _text_column(comments, fields.content_id)
    comments["content"] = _text_column(comments, "content")
    comments["nickname"] = _text_column(comments, "nickname")
    comments["user_id"] = _text_column(comments, "user_id")
    comments["comment_id"] = _text_column(comments, "comment_id")
    comments["like_count"] = _numeric_column(comments, "like_count")
    comments = _dedupe_by_id(comments, "comment_id", "like_count")
    return comments


def _dedupe_by_id(df: pd.DataFrame, id_column: str, sort_column: str) -> pd.DataFrame:
    if df.empty or id_column not in df.columns:
        return df
    blank = df[df[id_column].fillna("").astype(str).str.strip() == ""]
    with_id = df[df[id_column].fillna("").astype(str).str.strip() != ""]
    if with_id.empty:
        return df
    if sort_column in with_id.columns:
        with_id = with_id.sort_values(sort_column, ascending=True)
    with_id = with_id.drop_duplicates(subset=[id_column], keep="last").sort_index()
    return pd.concat([with_id, blank], ignore_index=True)


def _keyword_distribution(contents: pd.DataFrame) -> dict[str, int]:
    if contents.empty or "source_keyword" not in contents.columns:
        return {}
    values = contents["source_keyword"].fillna("").astype(str)
    values = values[values.str.strip() != ""]
    return {str(key): int(value) for key, value in values.value_counts().to_dict().items()}


def build_top_contents(contents: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if contents.empty:
        return pd.DataFrame()

    desired = [
        ("_content_id", "content_id"),
        ("title", "title"),
        ("nickname", "nickname"),
        ("_url", "url"),
        ("source_keyword", "source_keyword"),
        ("liked_count", "liked_count"),
        ("collected_count", "collected_count"),
        ("comment_count", "comment_count"),
        ("share_count", "share_count"),
        ("engagement_count", "engagement_count"),
    ]
    top = contents.sort_values("engagement_count", ascending=False).head(top_n)
    result = pd.DataFrame()
    for source, target in desired:
        result[target] = top[source] if source in top.columns else ""
    return result


def build_top_comments(comments: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if comments.empty:
        return pd.DataFrame()

    desired = [
        ("comment_id", "comment_id"),
        ("_content_id", "content_id"),
        ("nickname", "nickname"),
        ("content", "content"),
        ("like_count", "like_count"),
    ]
    top = comments.sort_values("like_count", ascending=False).head(top_n)
    result = pd.DataFrame()
    for source, target in desired:
        result[target] = top[source] if source in top.columns else ""
    return result


def _top_commented_contents(comments: pd.DataFrame, top_n: int) -> list[dict[str, Any]]:
    if comments.empty or "_content_id" not in comments.columns:
        return []
    counts = comments[comments["_content_id"].str.strip() != ""]["_content_id"].value_counts().head(top_n)
    return [{"content_id": str(key), "comment_count": int(value)} for key, value in counts.items()]


def _load_stop_words() -> set[str]:
    stop_words_path = Path(config.STOP_WORDS_FILE)
    if not stop_words_path.exists():
        return set()
    with stop_words_path.open("r", encoding="utf-8") as f:
        return {word.strip() for word in f if word.strip()}


def build_comment_word_frequency(comments: pd.DataFrame) -> Counter[str]:
    if comments.empty or "content" not in comments.columns:
        return Counter()

    stop_words = _load_stop_words()
    for word in getattr(config, "CUSTOM_WORDS", {}):
        jieba.add_word(word)

    all_text = " ".join(comments["content"].fillna("").astype(str))
    words = [
        word.strip()
        for word in jieba.lcut(all_text)
        if word.strip() and word.strip() not in stop_words
    ]
    return Counter(words)


def write_word_cloud(word_freq: Counter[str], output_path: Path) -> bool:
    if not word_freq:
        return False

    font_path = Path(config.FONT_PATH)
    wordcloud = WordCloud(
        font_path=str(font_path) if font_path.exists() else None,
        width=900,
        height=450,
        background_color="white",
        max_words=200,
        colormap="viridis",
    ).generate_from_frequencies(dict(word_freq.most_common(200)))

    plt.figure(figsize=(10, 5), facecolor="white")
    plt.imshow(wordcloud, interpolation="bilinear")
    plt.axis("off")
    plt.tight_layout(pad=0)
    plt.savefig(output_path, format="png", dpi=240)
    plt.close()
    return True


def _write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _dataframe_to_html_table(df: pd.DataFrame, empty_text: str) -> str:
    if df.empty:
        return f"<p>{html.escape(empty_text)}</p>"
    return df.to_html(index=False, escape=True, border=0, classes="data-table")


def _write_html_report(
    path: Path,
    platform: str,
    summary: dict[str, Any],
    top_contents: pd.DataFrame,
    top_comments: pd.DataFrame,
) -> None:
    metrics = summary["metrics"]
    keyword_items = "".join(
        f"<li>{html.escape(str(key))}: {int(value)}</li>"
        for key, value in summary["keyword_distribution"].items()
    ) or "<li>No keyword data</li>"

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>MediaCrawler Analysis Report - {html.escape(platform)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #202124; }}
    h1, h2 {{ margin-bottom: 12px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
    .metric {{ border: 1px solid #dadce0; border-radius: 6px; padding: 12px; }}
    .metric strong {{ display: block; font-size: 24px; margin-top: 6px; }}
    .data-table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
    .data-table th, .data-table td {{ border: 1px solid #dadce0; padding: 8px; text-align: left; }}
    .data-table th {{ background: #f8fafd; }}
  </style>
</head>
<body>
  <h1>MediaCrawler Analysis Report - {html.escape(platform)}</h1>
  <section class="metrics">
    <div class="metric">Contents<strong>{metrics["total_contents"]}</strong></div>
    <div class="metric">Comments<strong>{metrics["total_comments"]}</strong></div>
    <div class="metric">Creators<strong>{metrics["unique_creators"]}</strong></div>
    <div class="metric">Comment Users<strong>{metrics["unique_comment_users"]}</strong></div>
    <div class="metric">Avg Comment Likes<strong>{metrics["avg_comment_likes"]}</strong></div>
  </section>
  <h2>Keyword Distribution</h2>
  <ul>{keyword_items}</ul>
  <h2>Top Contents</h2>
  {_dataframe_to_html_table(top_contents, "No content data available.")}
  <h2>Top Comments</h2>
  {_dataframe_to_html_table(top_comments, "No comment data available.")}
</body>
</html>
"""
    path.write_text(html_doc, encoding="utf-8")


def generate_analysis_report(
    platform: str,
    contents_df: Optional[pd.DataFrame],
    comments_df: Optional[pd.DataFrame],
    output_dir: Path | str,
    top_n: int = 20,
) -> dict[str, Any]:
    """Generate offline analysis artifacts and return their paths."""

    if top_n <= 0:
        raise AnalysisReportError("top_n must be greater than 0")

    raw_total_contents = 0 if contents_df is None else int(len(contents_df))
    raw_total_comments = 0 if comments_df is None else int(len(comments_df))
    contents = normalize_contents(contents_df, platform)
    comments = normalize_comments(comments_df, platform)
    if contents.empty and comments.empty:
        raise AnalysisReportError("No content or comment data available for analysis")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    top_contents = build_top_contents(contents, top_n)
    top_comments = build_top_comments(comments, top_n)
    top_contents_path = output / "top_contents.csv"
    top_comments_path = output / "top_comments.csv"
    top_contents.to_csv(top_contents_path, index=False, encoding="utf-8-sig")
    top_comments.to_csv(top_comments_path, index=False, encoding="utf-8-sig")

    word_freq = build_comment_word_frequency(comments)
    word_freq_path = output / "comment_word_freq.json"
    _write_json(word_freq_path, {key: int(value) for key, value in word_freq.most_common()})

    word_cloud_path = output / "comment_word_cloud.png"
    word_cloud_generated = write_word_cloud(word_freq, word_cloud_path)

    metrics = {
        "raw_total_contents": raw_total_contents,
        "raw_total_comments": raw_total_comments,
        "total_contents": int(len(contents)),
        "total_comments": int(len(comments)),
        "duplicate_contents_removed": max(raw_total_contents - int(len(contents)), 0),
        "duplicate_comments_removed": max(raw_total_comments - int(len(comments)), 0),
        "deduped_comments": int(comments["comment_id"].nunique()) if "comment_id" in comments.columns else int(len(comments)),
        "unique_creators": int(contents["user_id"].nunique()) if "user_id" in contents.columns else 0,
        "unique_comment_users": int(comments["user_id"].nunique()) if "user_id" in comments.columns else 0,
        "avg_comment_likes": round(float(comments["like_count"].mean()), 2) if "like_count" in comments.columns and not comments.empty else 0.0,
    }
    summary = {
        "platform": platform,
        "top_n": top_n,
        "metrics": metrics,
        "keyword_distribution": _keyword_distribution(contents),
        "top_commented_contents": _top_commented_contents(comments, top_n),
        "outputs": {
            "summary": str(output / "summary.json"),
            "html": str(output / "report.html"),
            "top_contents": str(top_contents_path),
            "top_comments": str(top_comments_path),
            "comment_word_freq": str(word_freq_path),
            "comment_word_cloud": str(word_cloud_path) if word_cloud_generated else "",
        },
    }

    summary_path = output / "summary.json"
    html_path = output / "report.html"
    _write_json(summary_path, summary)
    _write_html_report(html_path, platform, summary, top_contents, top_comments)
    return summary
