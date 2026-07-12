import json
from pathlib import Path

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.dataset_service import DatasetService
from mediacrawler_mcp.normalizer import DatasetNormalizer
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.topic_research import TopicResearchService


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _research_dataset(tmp_path):
    config = McpConfig(home=tmp_path, browser_mode="persistent_context", cdp_endpoint=None, max_concurrent_tasks=1, default_timeout_seconds=300)
    storage = Storage(config)
    dataset = DatasetService(config, storage).create_dataset(
        name="Douyin deterministic research fixture",
        platforms=["douyin"],
        keywords=["Declared only"],
        options={"collection_task_id": "task_douyin_topic_fixture", "crawler_type": "search"},
    )
    contents = [
        {"aweme_id": "a1", "title": "#Cursor first #Ignored", "desc": "body", "aweme_type": "0", "user_id": "author1", "create_time": 1780000000, "aweme_url": "https://www.douyin.com/video/a1?token=private&keep=ok", "source_keyword": "Cursor编辑器", "liked_count": "10", "comment_count": "2", "collected_count": "3", "share_count": "4"},
        {"aweme_id": "a2", "title": "no title tag", "desc": "learn #AI编程 next", "aweme_type": "0", "user_id": "author2", "create_time": 1780000001, "aweme_url": "https://www.douyin.com/video/a2", "source_keyword": "Cursor编辑器", "liked_count": "2", "comment_count": "0", "collected_count": "0", "share_count": "0"},
        {"aweme_id": "a3", "title": "#123 #GPT-5", "desc": "body", "aweme_type": "0", "user_id": "author3", "create_time": 1780000002, "aweme_url": "https://www.douyin.com/video/a3", "source_keyword": "fallback", "liked_count": "bad", "comment_count": "bad", "collected_count": "bad", "share_count": "bad"},
        {"aweme_id": "a4", "title": "#00:13 #3D打印", "desc": "body", "aweme_type": "0", "user_id": "author4", "create_time": 1780000003, "aweme_url": "https://www.douyin.com/video/a4", "source_keyword": "fallback", "liked_count": "0", "comment_count": "0", "collected_count": "0", "share_count": "0"},
        {"aweme_id": "a5", "title": "no explicit topic", "desc": "body", "aweme_type": "0", "user_id": "author5", "create_time": 1780000004, "aweme_url": "https://www.douyin.com/video/a5", "source_keyword": "", "liked_count": None, "comment_count": None, "collected_count": None, "share_count": None},
    ]
    comments = [
        {"comment_id": "root", "aweme_id": "a1", "content": "很好用，值得推荐", "user_id": "reader1", "like_count": "9", "create_time": 1780000010, "parent_comment_id": "0"},
        {"comment_id": "reply", "aweme_id": "a1", "content": "请问怎么下单，想试试", "user_id": "reader2", "like_count": "8", "create_time": 1780000011, "parent_comment_id": "root"},
        {"comment_id": "negative", "aweme_id": "a2", "content": "太贵，担心不好用", "user_id": "reader3", "like_count": "2", "create_time": 1780000012, "parent_comment_id": "0"},
    ]
    raw_dir = Path(dataset.dataset_dir) / "raw"
    _write_jsonl(raw_dir / "douyin_contents.jsonl", contents)
    _write_jsonl(raw_dir / "douyin_comments.jsonl", comments)
    DatasetNormalizer(storage).normalize_dataset(dataset.dataset_id)
    return dataset, storage, TopicResearchService(storage)


def test_douyin_topics_use_explicit_hashtags_then_source_keyword_and_safe_evidence(tmp_path):
    dataset, _, research = _research_dataset(tmp_path)

    summary = research.generate_topic_research_report(dataset.dataset_id)["summary"]
    topics = {topic["name"]: topic for topic in summary["topics"]}

    assert set(topics) == {"Cursor", "AI编程", "GPT-5", "3D打印", "未分类"}
    assert topics["Cursor"]["content_count"] == 1
    assert topics["Cursor"]["grouping_method"] == "explicit_hashtag_then_source_keyword_deterministic_grouping"
    root = next(item for item in topics["Cursor"]["representative_comments"] if item["comment_id"] == "root")
    reply = next(item for item in topics["Cursor"]["representative_comments"] if item["comment_id"] == "reply")
    assert root["parent_comment_id"] is None
    assert reply["parent_comment_id"] == "root"
    assert root["content_id"] == reply["content_id"] == "a1"
    assert root["source_keyword"] == "Cursor编辑器"
    assert "token" not in root["content_url"]
    assert {"comment_id", "content_id", "parent_comment_id", "comment_text", "like_count", "source_keyword", "content_url"}.issubset(root)


def test_douyin_topic_report_is_idempotent_and_declares_platform_limits(tmp_path):
    dataset, storage, research = _research_dataset(tmp_path)

    first = research.generate_topic_research_report(dataset.dataset_id)
    second = research.generate_topic_research_report(dataset.dataset_id)
    summary = first["summary"]
    report_json = Path(first["report_json_path"])
    markdown = Path(first["report_md_path"])
    html = Path(first["report_html_path"])

    assert first["report_id"] == second["report_id"]
    assert report_json.name == "topic_research.json"
    assert report_json.exists() and markdown.exists() and html.exists()
    assert summary["platform"] == "douyin"
    assert summary["dataset_id"] == dataset.dataset_id
    assert summary["scope"]["declared_source_keywords"] == ["Declared only"]
    assert summary["scope"]["observed_source_keywords"] == ["Cursor编辑器", "fallback"]
    assert summary["data_quality"]["pipeline_counts"]["raw_content_count"] == 5
    assert summary["data_quality"]["pipeline_counts"]["raw_comment_count"] == 3
    assert summary["data_quality"]["interaction_field_status_counts"]["parse_error"] == 1
    assert summary["data_quality"]["interaction_field_status_counts"]["missing"] == 1
    rendered = "\n".join([report_json.read_text(encoding="utf-8"), markdown.read_text(encoding="utf-8"), html.read_text(encoding="utf-8")]).lower()
    for forbidden in ("token=", "sec_uid", "nickname", "avatar", "ip_location", "douyinvod"):
        assert forbidden not in rendered
    assert "raw comments do not contain source_keyword" in rendered
    assert "not a trend analysis" in rendered
    assert "no llm" in rendered
    assert storage.get_report_row(dataset.dataset_id)["report_id"] == first["report_id"]
