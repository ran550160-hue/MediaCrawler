import json
import importlib.util
from pathlib import Path

import duckdb
import pytest

from mediacrawler_mcp import server
from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.dataset_bundle_exporter import DatasetBundleExporter
from mediacrawler_mcp.dataset_importer import DatasetImporter
from mediacrawler_mcp.dataset_service import DatasetService
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.normalizer import DatasetNormalizer
from mediacrawler_mcp.normalizer import _number_with_status
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.topic_research import TopicResearchService, build_topic_analysis, calculate_evidence_strength, classify_comment_view


FIXTURES = Path(__file__).parent / "fixtures" / "xhs"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _services(tmp_path):
    config = McpConfig(home=tmp_path, browser_mode="persistent_context", cdp_endpoint=None, max_concurrent_tasks=1, default_timeout_seconds=300)
    storage = Storage(config)
    return config, storage, DatasetService(config, storage), DatasetNormalizer(storage), TopicResearchService(storage)


def _dataset_with_rows(tmp_path, contents, comments, name="规则型研究"):
    _, _, datasets, normalizer, research = _services(tmp_path)
    dataset = datasets.create_dataset(
        name=name,
        platforms=["xhs"],
        keywords=["AI 编程"],
        options={"collection_task_id": "task_xhs_demo_001", "collection_started_at": "2026-07-11T10:00:00+00:00", "collection_completed_at": "2026-07-11T10:05:00+00:00", "crawler_type": "search"},
    )
    raw_dir = tmp_path / "datasets" / dataset.dataset_id / "raw"
    _write_jsonl(raw_dir / "xhs_contents.jsonl", contents)
    _write_jsonl(raw_dir / "xhs_comments.jsonl", comments)
    normalizer.normalize_dataset(dataset.dataset_id)
    return dataset, research


def _one_post_three_comments_rows():
    return (
        [{"note_id": "note-1", "note_url": "https://example.test/xhs/note-1", "title": "AI 编程工具体验", "desc": "适合新手 #AI编程[话题]#", "user_id": "author-a", "type": "normal", "time": "1783747360662", "source_keyword": "AI 编程", "interact_info": {"liked_count": "120", "collected_count": "20", "comment_count": "3", "share_count": "4"}}],
        [
            {"note_id": "note-1", "comment_id": "comment-positive", "content": "很好用，值得推荐", "user_id": "reader-1", "like_count": "8"},
            {"note_id": "note-1", "comment_id": "comment-negative", "content": "想买但太贵", "user_id": "reader-2", "like_count": "12"},
            {"note_id": "note-1", "comment_id": "comment-question", "content": "请问想买的话怎么下单？", "user_id": "reader-3", "like_count": "2"},
        ],
    )


def test_representative_posts_and_comments_never_mix_when_post_count_is_less_than_three(tmp_path):
    contents, comments = _one_post_three_comments_rows()
    dataset, research = _dataset_with_rows(tmp_path, contents, comments)

    result = research.generate_topic_research_report(dataset.dataset_id)
    topic = result["summary"]["topic_analysis"]["topics"][0]

    assert [item["content_type"] for item in topic["representative_posts"]] == ["normal"]
    assert {item["content_type"] for item in topic["representative_comments"]} == {"comment"}
    assert len(topic["representative_posts"]) == 1
    assert len(topic["representative_comments"]) == 3
    html_report = Path(result["report_html_path"]).read_text(encoding="utf-8")
    assert 'href="https://example.test/xhs/note-1"' in html_report


def test_interaction_quality_distinguishes_zero_missing_parse_error_and_nested_nonzero(tmp_path):
    contents = [
        {"note_id": "zero", "note_url": "https://example.test/zero", "source_keyword": "AI", "liked_count": "0", "collected_count": "0", "comment_count": "0", "share_count": "0"},
        {"note_id": "missing", "note_url": "https://example.test/missing", "source_keyword": "AI"},
        {"note_id": "bad", "note_url": "https://example.test/bad", "source_keyword": "AI", "liked_count": "not-a-number", "collected_count": "also-bad"},
        {"note_id": "nested", "note_url": "https://example.test/nested", "source_keyword": "AI", "note_card": {"interact_info": {"liked_count": "9", "comment_count": "0"}}},
    ]
    dataset, research = _dataset_with_rows(tmp_path, contents, [])
    database = tmp_path / "datasets" / dataset.dataset_id / "analysis.duckdb"
    with duckdb.connect(str(database), read_only=True) as conn:
        rows = conn.execute("SELECT content_id, like_count, interaction_field_status FROM contents ORDER BY content_id").fetchall()
    assert rows == [("bad", 0, "parse_error"), ("missing", 0, "missing"), ("nested", 9, "present"), ("zero", 0, "present_zero")]
    quality = research.generate_topic_research_report(dataset.dataset_id)["summary"]["data_quality"]
    assert quality["interaction_field_status_counts"] == {"present": 1, "present_zero": 1, "missing": 1, "parse_error": 1, "partial_parse_error": 0, "present_approximate": 0}
    assert quality["anomaly_type_counts"]["missing_interaction_fields"] == 1
    assert quality["anomaly_type_counts"]["interaction_parse_error"] == 1


def test_anomaly_sample_count_uses_unique_record_ids(tmp_path):
    contents = [{"note_id": "bad-once", "source_keyword": "AI"}]
    comments = [{"note_id": "unknown", "comment_id": "orphan", "content": "orphan"}]
    dataset, research = _dataset_with_rows(tmp_path, contents, comments)

    quality = research.generate_topic_research_report(dataset.dataset_id)["summary"]["data_quality"]

    assert quality["anomaly_type_counts"] == {"missing_source_url": 1, "missing_interaction_fields": 1, "interaction_parse_error": 0, "orphan_comment": 1}
    assert quality["anomaly_sample_count"] == 2


def test_comment_view_facets_keep_question_intent_and_negative_information():
    question_intent = classify_comment_view("请问想买的话怎么下单？")
    expensive_intent = classify_comment_view("想买但太贵")
    assert question_intent["sentiment"] == "neutral"
    assert question_intent["is_question"] is True
    assert question_intent["has_action_intent"] is True
    assert expensive_intent["sentiment"] == "negative"
    assert expensive_intent["has_action_intent"] is True
    assert classify_comment_view("不推荐，真的不太好用")["sentiment"] == "negative"
    assert classify_comment_view("不是很贵")["sentiment"] == "neutral"


def test_contrasting_views_have_one_positive_and_one_negative_example(tmp_path):
    contents, comments = _one_post_three_comments_rows()
    dataset, research = _dataset_with_rows(tmp_path, contents, comments)

    contrasting = research.generate_topic_research_report(dataset.dataset_id)["summary"]["topic_analysis"]["topics"][0]["contrasting_view_examples"]

    assert contrasting["positive_example"]["view"]["sentiment"] == "positive"
    assert contrasting["negative_example"]["view"]["sentiment"] == "negative"


def test_reply_evidence_keeps_parent_post_and_parent_comment_context(tmp_path):
    contents = [{"note_id": "note-1", "note_url": "https://example.test/xhs/note-1", "title": "父帖", "source_keyword": "AI", "liked_count": "1"}]
    comments = [{"note_id": "note-1", "comment_id": "parent-comment", "content": "一级评论", "like_count": "1", "sub_comments": [{"id": "reply-comment", "content": "回复评论", "like_count": "2"}]}]
    dataset, research = _dataset_with_rows(tmp_path, contents, comments)

    topic = research.generate_topic_research_report(dataset.dataset_id)["summary"]["topic_analysis"]["topics"][0]
    reply = next(item for item in topic["representative_comments"] if item["content_id"] == "reply-comment")

    assert reply["content_type"] == "reply"
    assert reply["parent_content_id"] == "note-1"
    assert reply["parent_comment_id"] == "parent-comment"


def test_report_declares_deterministic_grouping_and_escapes_unsafe_evidence_urls(tmp_path):
    contents = [{"note_id": "unsafe", "note_url": "javascript:alert(1)", "title": "<unsafe>", "desc": "text", "source_keyword": "AI", "liked_count": "0"}]
    dataset, research = _dataset_with_rows(tmp_path, contents, [])

    result = research.generate_topic_research_report(dataset.dataset_id)
    summary = result["summary"]
    html_report = Path(result["report_html_path"]).read_text(encoding="utf-8")
    markdown_report = Path(result["report_md_path"]).read_text(encoding="utf-8")
    topic = summary["topic_analysis"]["topics"][0]
    assert topic["grouping_method"] == "tag_then_source_keyword_deterministic_grouping"
    assert "No semantic clustering is used." in topic["grouping_limitations"]
    assert "javascript:" not in html_report
    assert "&lt;unsafe&gt;" in html_report
    assert "not semantic topic clustering" in markdown_report


def test_real_capture_sanitized_fixture_integrates_through_normalization_and_report(tmp_path):
    content = json.loads((FIXTURES / "real_capture_sanitized_contents.jsonl").read_text(encoding="utf-8"))
    comment = json.loads((FIXTURES / "real_capture_sanitized_comments.jsonl").read_text(encoding="utf-8"))
    dataset, research = _dataset_with_rows(tmp_path, [content], [comment], name="真实结构脱敏样本")

    result = research.generate_topic_research_report(dataset.dataset_id)

    assert result["summary"]["scope"]["post_count"] == 1
    assert result["summary"]["scope"]["comment_count"] == 1
    scope = result["summary"]["scope"]
    topic = result["summary"]["topic_analysis"]["topics"][0]
    evidence = topic["representative_comments"][0]
    assert evidence["parent_content_id"] == content["note_id"]
    assert evidence["parent_comment_id"] is None
    assert topic["total_engagement"] == 120957
    assert topic["representative_posts"][0]["engagement"] == 116331
    assert scope["collection_time_range"]["from"].startswith("2026-")
    assert scope["observed_source_keywords"] == [content["source_keyword"]]
    markdown = Path(result["report_md_path"]).read_text(encoding="utf-8")
    assert "父评论=0" not in markdown


def test_fixture_has_no_sensitive_urls_or_identity_markers():
    payload = "\n".join((FIXTURES / name).read_text(encoding="utf-8").lower() for name in ("real_capture_sanitized_contents.jsonl", "real_capture_sanitized_comments.jsonl"))
    for forbidden in ("xhscdn.com", "xsec_", "sign=", "cookie", "device", "ip_location"):
        assert forbidden not in payload
    assert "https://example.test" in payload


def test_abbreviated_interaction_numbers_and_partial_parse_errors_are_explainable(tmp_path):
    contents = [
        {"note_id": "ten-wan", "note_url": "https://example.test/1", "source_keyword": "AI", "liked_count": "10万+"},
        {"note_id": "one-point-one", "note_url": "https://example.test/2", "source_keyword": "AI", "liked_count": "1.1万+"},
        {"note_id": "partial", "note_url": "https://example.test/3", "source_keyword": "AI", "liked_count": "bad", "comment_count": "3"},
    ]
    dataset, research = _dataset_with_rows(tmp_path, contents, [])
    database = tmp_path / "datasets" / dataset.dataset_id / "analysis.duckdb"
    with duckdb.connect(str(database), read_only=True) as conn:
        rows = conn.execute("SELECT content_id, like_count, interaction_field_status, interaction_approximate_fields, interaction_parse_error_fields FROM contents ORDER BY content_id").fetchall()
    assert rows[0] == ("one-point-one", 11000, "present_approximate", '["like_count"]', "[]")
    assert rows[1][2] == "partial_parse_error"
    assert rows[2] == ("ten-wan", 100000, "present_approximate", '["like_count"]', "[]")
    quality = research.generate_topic_research_report(dataset.dataset_id)["summary"]["data_quality"]
    assert quality["interaction_parse_error_fields"]["partial"] == ["like_count"]
    assert quality["interaction_approximate_fields"]["ten-wan"] == ["like_count"]


def test_number_parser_supports_xhs_abbreviations_commas_and_decimals():
    assert _number_with_status("10万+") == (100000, True, True)
    assert _number_with_status("1.1万+") == (11000, True, True)
    assert _number_with_status("10w+") == (100000, True, True)
    assert _number_with_status("1.2W+") == (12000, True, True)
    assert _number_with_status("3千+") == (3000, True, True)
    assert _number_with_status("2k+") == (2000, True, True)
    assert _number_with_status("1,234.9") == (1234, True, False)


def test_sanitizer_selects_a_real_matching_pair_before_pseudonymizing(tmp_path):
    script_path = FIXTURES / "sanitize_local_xhs_fixture.py"
    spec = importlib.util.spec_from_file_location("sanitize_fixture", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    contents_path, comments_path = tmp_path / "contents.jsonl", tmp_path / "comments.jsonl"
    _write_jsonl(contents_path, [{"note_id": "raw-linked", "title": "private title", "liked_count": "10万+"}, {"note_id": "other"}])
    _write_jsonl(comments_path, [{"note_id": "other-missing", "comment_id": "skip"}, {"note_id": "raw-linked", "comment_id": "raw-comment", "content": "private comment"}])

    source_content, source_comment = module.select_related_records(contents_path, comments_path)
    clean_content, clean_comment = module.sanitize_related_pair(contents_path, comments_path)

    assert source_content["note_id"] == source_comment["note_id"] == "raw-linked"
    assert clean_content["note_id"] == clean_comment["note_id"]
    assert clean_content["title"] == "synthetic fixture title"


def test_empty_ids_do_not_merge_distinct_anomalies(tmp_path):
    contents = [{"note_id": "", "source_keyword": "AI"}, {"note_id": "", "source_keyword": "AI"}]
    dataset, research = _dataset_with_rows(tmp_path, contents, [])
    quality = research.generate_topic_research_report(dataset.dataset_id)["summary"]["data_quality"]
    assert quality["anomaly_sample_count"] == 2


def test_outdated_duckdb_schema_requires_force_renormalization_before_topic_research(tmp_path):
    _, _, datasets, _, research = _services(tmp_path)
    dataset = datasets.create_dataset(name="旧 schema", platforms=["xhs"], keywords=["AI"])
    database = Path(dataset.dataset_dir) / "analysis.duckdb"
    with duckdb.connect(str(database)) as conn:
        conn.execute("CREATE TABLE contents (dataset_id TEXT, content_id TEXT, engagement_count BIGINT)")
        conn.execute("CREATE TABLE comments (content_id TEXT, comment_id TEXT, comment_text TEXT)")
        conn.execute("INSERT INTO contents VALUES ('old', 'n1', 0)")

    with pytest.raises(McpAppError) as exc_info:
        research.generate_topic_research_report(dataset.dataset_id)

    assert exc_info.value.code == ErrorCode.REPORT_FAILED
    assert exc_info.value.message == "Dataset schema is outdated. Run normalize_dataset(dataset_id, force=True) before topic research."


def test_topic_analysis_is_order_independent_and_evidence_strength_is_rule_based():
    contents = [{"content_id": "n1", "source_keyword": "话题", "tags": "[]", "engagement_count": 9, "title": "标题", "content_text": "同一内容", "url": "https://example.test/n1"}]
    comments = [{"content_id": "n1", "comment_id": "c1", "comment_text": "值得推荐", "like_count": 2, "user_id": "u1"}]
    assert build_topic_analysis(contents, comments, "ds") == build_topic_analysis(list(reversed(contents)), list(reversed(comments)), "ds")
    assert calculate_evidence_strength(3, 5, 0)["level"] == "high"
    assert calculate_evidence_strength(0, 0, 1)["level"] == "low"


@pytest.mark.parametrize(
    ("tags", "source_keyword", "expected"),
    [
        (["00:13", "有效标签"], "fallback", "有效标签"),
        (["01:25:30", "有效标签"], "fallback", "有效标签"),
        (["123", "有效标签"], "fallback", "有效标签"),
        (["GPT-5"], "fallback", "GPT-5"),
        (["3D打印"], "fallback", "3D打印"),
        (["2026世界杯"], "fallback", "2026世界杯"),
        (["00:13", "123"], "fallback", "fallback"),
    ],
)
def test_topic_grouping_ignores_only_numeric_or_duration_like_tags(tags, source_keyword, expected):
    contents = [{"content_id": "n1", "tags": json.dumps(tags, ensure_ascii=False), "source_keyword": source_keyword}]

    topic = build_topic_analysis(contents, [], "ds")["topics"][0]

    assert topic["name"] == expected


def test_finalize_local_xhs_search_generates_real_topic_report_after_mocked_local_edge(tmp_path, monkeypatch):
    contents, comments = _one_post_three_comments_rows()
    source_dir = tmp_path / "source"
    _write_jsonl(source_dir / "contents.jsonl", contents)
    _write_jsonl(source_dir / "comments.jsonl", comments)
    bundle = DatasetBundleExporter().export_xhs_bundle(name="Finalize topic", output_dir=tmp_path / "bundle", keywords=["AI 编程"], contents_path=source_dir / "contents.jsonl", comments_path=source_dir / "comments.jsonl", dataset_id="finalize_topic", collection_task_id="task-finalize")
    config, storage, _, _, _ = _services(tmp_path / "home")
    importer = DatasetImporter(config, storage)

    class FakeDesktopAgentClient:
        def __init__(self, base_url=None):
            pass

        def finalize_task(self, task_id, dataset_name="", description="", force=False):
            return {"dataset_dir": bundle["dataset_dir"], "files": []}

    monkeypatch.setattr(server, "DesktopAgentClient", FakeDesktopAgentClient)
    monkeypatch.setattr(server, "_importer", lambda: importer)
    monkeypatch.setattr(server, "_storage", lambda: storage)

    result = server.finalize_local_xhs_search("agent_task", report_type="topic_research")

    assert result["status"] == "success"
    assert result["report_type"] == "topic_research"
    assert result["report"]["summary"]["report_type"] == "topic_research"
    assert Path(result["report"]["summary_json_path"]).exists()
