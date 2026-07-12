import json
from pathlib import Path

import duckdb
import pytest

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.dataset_bundle_exporter import DatasetBundleExporter
from mediacrawler_mcp.dataset_importer import DatasetImporter
from mediacrawler_mcp.dataset_service import DatasetService
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.normalizer import DatasetNormalizer
from mediacrawler_mcp.storage import Storage


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "douyin"


def _services(tmp_path):
    config = McpConfig(
        home=tmp_path,
        browser_mode="persistent_context",
        cdp_endpoint=None,
        max_concurrent_tasks=1,
        default_timeout_seconds=300,
    )
    storage = Storage(config)
    return (
        DatasetService(config, storage),
        DatasetImporter(config, storage),
        DatasetNormalizer(storage),
    )


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _make_douyin_dataset(tmp_path, contents, comments, *, name="Douyin normalize fixture", platforms=("douyin",), keywords=("Cursor",), manifest_extra=None):
    dataset_service, importer, normalizer = _services(tmp_path)
    dataset = dataset_service.create_dataset(
        name=name,
        platforms=list(platforms),
        keywords=list(keywords),
    )
    raw_dir = tmp_path / "datasets" / dataset.dataset_id / "raw"
    _write_jsonl(raw_dir / "douyin_contents.jsonl", contents)
    if comments is not None:
        _write_jsonl(raw_dir / "douyin_comments.jsonl", comments)
    manifest_path = tmp_path / "datasets" / dataset.dataset_id / "dataset.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("options", {})
    manifest["options"].update({"collection_task_id": "task_dy_test_001"})
    if manifest_extra:
        manifest.update(manifest_extra)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return dataset, normalizer, importer


def _one_content_two_level_comments():
    contents = [
        {
            "aweme_id": "7400000000000000001",
            "title": "synthetic_douyin_content_title",
            "desc": "synthetic_douyin_content_description_fixture",
            "aweme_type": "0",
            "user_id": "user_fake_0001",
            "nickname": "synthetic_douyin_nickname_fixture",
            "create_time": 1780000000,
            "aweme_url": "https://www.douyin.com/video/7400000000000000001",
            "source_keyword": "Cursor",
            "last_modify_ts": 1780000000000,
            "liked_count": "100",
            "comment_count": "10",
            "collected_count": "5",
            "share_count": "2",
        }
    ]
    comments = [
        {
            "comment_id": "c0001",
            "aweme_id": "7400000000000000001",
            "content": "synthetic first-level comment",
            "user_id": "user_fake_0001",
            "like_count": 5,
            "create_time": 1780000001,
            "sub_comment_count": "2",
            "parent_comment_id": "0",
            "last_modify_ts": 1780000000001,
        },
        {
            "comment_id": "c0002",
            "aweme_id": "7400000000000000001",
            "content": "synthetic reply one",
            "user_id": "user_fake_0002",
            "like_count": "3",
            "create_time": 1780000002,
            "sub_comment_count": "0",
            "parent_comment_id": "c0001",
            "last_modify_ts": 1780000000002,
        },
        {
            "comment_id": "c0003",
            "aweme_id": "7400000000000000001",
            "content": "synthetic reply two",
            "user_id": "user_fake_0003",
            "like_count": "1",
            "create_time": 1780000003,
            "sub_comment_count": "0",
            "parent_comment_id": "c0001",
            "last_modify_ts": 1780000000003,
        },
    ]
    return contents, comments


def _open_duckdb(summary):
    return duckdb.connect(summary["duckdb_path"], read_only=True)


# Content mapping tests (cases 1-9)


def test_douyin_contents_written_to_duckdb(tmp_path):
    contents, _ = _one_content_two_level_comments()
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    assert summary["content_count"] == 1
    assert summary["comment_count"] == 0
    with _open_duckdb(summary) as conn:
        row = conn.execute("SELECT content_id, platform, source_keyword, content_type, url, engagement_count FROM contents").fetchone()
    assert row is not None
    assert row[0] == "7400000000000000001"
    assert row[1] == "douyin"
    assert row[2] == "Cursor"


def test_aweme_id_maps_to_content_id(tmp_path):
    contents = [{"aweme_id": "7900000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000}]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        content_id = conn.execute("SELECT content_id FROM contents").fetchone()[0]
    assert content_id == "7900000000000000001"


def test_title_desc_fallback_when_empty(tmp_path):
    contents = [
        {"aweme_id": "7400000000000000001", "title": "", "desc": "only_desc_here", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000, "liked_count": "0", "comment_count": "0", "collected_count": "0", "share_count": "0"},
        {"aweme_id": "7400000000000000002", "title": "only_title_here", "desc": "", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000, "liked_count": "0", "comment_count": "0", "collected_count": "0", "share_count": "0"},
    ]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        desc_row1 = conn.execute("SELECT content_id, title, \"desc\", content_text FROM contents WHERE content_id = '7400000000000000001'").fetchone()
        desc_row2 = conn.execute("SELECT content_id, title, \"desc\", content_text FROM contents WHERE content_id = '7400000000000000002'").fetchone()
    assert desc_row1[2] == "only_desc_here"
    assert desc_row2[3] == "only_title_here"


def test_second_create_time_parsed_as_seconds(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000}]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        dt = conn.execute("SELECT publish_datetime FROM contents").fetchone()[0]
    assert dt.year == 2026


def test_millisecond_crawl_time_parsed(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000}]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        crawl = conn.execute("SELECT crawl_time FROM contents").fetchone()[0]
    assert crawl == "1780000000000"


def test_url_strips_sensitive_params_and_preserves_path(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000, "aweme_url": "https://www.douyin.com/video/7400000000000000001?msToken=SECRET&a_bogus=xyz&xsec_token=ABC"}]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        url = conn.execute("SELECT url FROM contents").fetchone()[0]
    assert "SECRET" not in url
    assert "msToken" not in url
    assert "a_bogus" not in url
    assert "xsec_token" not in url
    assert url.startswith("https://www.douyin.com/video/7400000000000000001")


def test_url_fallback_constructs_from_aweme_id_when_missing(tmp_path):
    contents = [{"aweme_id": "7400000000000000099", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000}]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        url = conn.execute("SELECT url FROM contents").fetchone()[0]
    assert url == "https://www.douyin.com/video/7400000000000000099"


def test_interaction_counts_parsed_from_strings(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000, "liked_count": "52067", "comment_count": "1312", "collected_count": "48956", "share_count": "10405"}]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        like, comment, collect, share, engagement = conn.execute(
            "SELECT like_count, comment_count, collect_count, share_count, engagement_count FROM contents"
        ).fetchone()
    assert like == 52067
    assert comment == 1312
    assert collect == 48956
    assert share == 10405
    assert engagement == 52067 + 1312 + 48956 + 10405


def test_engagement_excludes_play_count(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000, "liked_count": "10", "comment_count": "2", "collected_count": "3", "share_count": "1", "play_count": "999999"}]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        engagement = conn.execute("SELECT engagement_count FROM contents").fetchone()[0]
    assert engagement == 10 + 2 + 3 + 1


# Interaction status taxonomy (cases 10-14)


def test_interaction_status_present(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000, "liked_count": "100", "comment_count": "10", "collected_count": "5", "share_count": "2"}]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        status = conn.execute("SELECT interaction_field_status FROM contents").fetchone()[0]
    assert status == "present"


def test_interaction_status_approximate(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000, "liked_count": "1万+", "comment_count": "10", "collected_count": "5", "share_count": "2"}]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        row = conn.execute("SELECT interaction_field_status, interaction_approximate_fields FROM contents").fetchone()
    assert row[0] == "present_approximate"
    assert "like_count" in json.loads(row[1])


def test_interaction_status_missing(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000}]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        status = conn.execute("SELECT interaction_field_status, engagement_count FROM contents").fetchone()
    assert status[0] == "missing"
    assert status[1] == 0


def test_interaction_status_parse_error(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000, "liked_count": "not_a_number", "comment_count": "also_bad", "collected_count": "x", "share_count": "y"}]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        row = conn.execute("SELECT interaction_field_status, interaction_parse_error_fields FROM contents").fetchone()
    assert row[0] == "parse_error"
    errors = json.loads(row[1])
    assert "like_count" in errors and "comment_count" in errors


def test_interaction_status_partial_parse_error(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000, "liked_count": "100", "comment_count": "good", "collected_count": "bad_value", "share_count": "2"}]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        row = conn.execute("SELECT interaction_field_status, interaction_parse_error_fields, interaction_approximate_fields FROM contents").fetchone()
    assert row[0] == "partial_parse_error"
    assert "collect_count" in json.loads(row[1])


# Comments and source_keyword inheritance (cases 15-21)


def test_raw_comment_no_source_keyword_but_normalized_inherits(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "Cursor", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000}]
    comments = [{"comment_id": "c1", "aweme_id": "7400000000000000001", "content": "msg", "user_id": "u2", "like_count": 1, "create_time": 1780000001, "parent_comment_id": "0", "last_modify_ts": 1780000000001}]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, comments)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        row = conn.execute("SELECT source_keyword, raw_json FROM comments WHERE comment_id = 'c1'").fetchone()
    assert "source_keyword" not in json.loads(row[1])
    assert row[0] == "Cursor"


def test_first_level_parent_comment_id_normalized_to_null(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000}]
    comments = [
        {"comment_id": "c1", "aweme_id": "7400000000000000001", "content": "first", "user_id": "u", "like_count": 1, "create_time": 1780000001, "parent_comment_id": "0", "last_modify_ts": 1780000000001},
        {"comment_id": "c2", "aweme_id": "7400000000000000001", "content": "first", "user_id": "u", "like_count": 1, "create_time": 1780000001, "parent_comment_id": 0, "last_modify_ts": 1780000000001},
        {"comment_id": "c3", "aweme_id": "7400000000000000001", "content": "first", "user_id": "u", "like_count": 1, "create_time": 1780000001, "parent_comment_id": "", "last_modify_ts": 1780000000001},
    ]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, comments)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        parents = conn.execute("SELECT comment_id, parent_comment_id FROM comments ORDER BY comment_id").fetchall()
    for cid, parent in parents:
        assert parent is None


def test_second_level_parent_comment_id_preserved(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000}]
    comments = [
        {"comment_id": "c1", "aweme_id": "7400000000000000001", "content": "first", "user_id": "u", "like_count": 5, "create_time": 1780000001, "parent_comment_id": "0", "last_modify_ts": 1780000000001},
        {"comment_id": "c2", "aweme_id": "7400000000000000001", "content": "reply", "user_id": "u", "like_count": 1, "create_time": 1780000002, "parent_comment_id": "c1", "last_modify_ts": 1780000000002},
    ]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, comments)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        rows = conn.execute("SELECT comment_id, parent_comment_id FROM comments ORDER BY comment_id").fetchall()
    parents = {cid: parent for cid, parent in rows}
    assert parents["c1"] is None
    assert parents["c2"] == "c1"


def test_second_level_parent_matches_first_level_comment(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000}]
    _, comments_fixture = _one_content_two_level_comments()
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, comments_fixture)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        roots = conn.execute("SELECT comment_id FROM comments WHERE parent_comment_id IS NULL").fetchall()
        replies = conn.execute("SELECT parent_comment_id FROM comments WHERE parent_comment_id IS NOT NULL").fetchall()
    root_ids = {r[0] for r in roots}
    reply_parents = {r[0] for r in replies}
    assert reply_parents.issubset(root_ids)
    assert summary["reply_lineage_anomaly_count"] == 0


def test_orphan_comment_detected(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000}]
    comments = [
        {"comment_id": "ok", "aweme_id": "7400000000000000001", "content": "ok", "user_id": "u", "like_count": 1, "create_time": 1780000001, "parent_comment_id": "0", "last_modify_ts": 1780000000001},
        {"comment_id": "orphan", "aweme_id": "9999999999999999999", "content": "orphan", "user_id": "u", "like_count": 1, "create_time": 1780000001, "parent_comment_id": "0", "last_modify_ts": 1780000000001},
    ]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, comments)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    assert summary["orphan_comment_count"] == 1
    with _open_duckdb(summary) as conn:
        orphan_source = conn.execute("SELECT source_keyword FROM comments WHERE comment_id = 'orphan'").fetchone()[0]
    assert orphan_source == ""


def test_reply_lineage_anomaly_detected(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000}]
    comments = [
        {"comment_id": "real_root", "aweme_id": "7400000000000000001", "content": "first", "user_id": "u", "like_count": 1, "create_time": 1780000001, "parent_comment_id": "0", "last_modify_ts": 1780000000001},
        {"comment_id": "bad_reply", "aweme_id": "7400000000000000001", "content": "reply", "user_id": "u", "like_count": 1, "create_time": 1780000002, "parent_comment_id": "ghost_parent", "last_modify_ts": 1780000000002},
    ]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, comments)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    assert summary["reply_lineage_anomaly_count"] == 1


# De-dup and blank-ID safety (cases 22-24)


def test_content_id_dedup_keeps_higher_engagement(tmp_path):
    contents = [
        {"aweme_id": "7400000000000000001", "title": "lower", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000, "liked_count": "10", "comment_count": "0", "collected_count": "0", "share_count": "0"},
        {"aweme_id": "7400000000000000001", "title": "higher", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000, "liked_count": "999", "comment_count": "0", "collected_count": "0", "share_count": "0"},
    ]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    assert summary["content_count"] == 1
    assert summary["deduplicated_content_count"] == 1
    with _open_duckdb(summary) as conn:
        title = conn.execute("SELECT title FROM contents").fetchone()[0]
    assert title == "higher"


def test_comment_id_dedup_keeps_higher_like(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000}]
    comments = [
        {"comment_id": "dup", "aweme_id": "7400000000000000001", "content": "lower", "user_id": "u", "like_count": 1, "create_time": 1780000001, "parent_comment_id": "0", "last_modify_ts": 1780000000001},
        {"comment_id": "dup", "aweme_id": "7400000000000000001", "content": "higher", "user_id": "u", "like_count": 50, "create_time": 1780000001, "parent_comment_id": "0", "last_modify_ts": 1780000000001},
    ]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, comments)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    assert summary["comment_count"] == 1
    with _open_duckdb(summary) as conn:
        text = conn.execute("SELECT comment_text FROM comments").fetchone()[0]
    assert text == "higher"


def test_blank_content_ids_not_merged_into_each_other(tmp_path):
    contents = [
        {"aweme_id": "", "title": "blank1", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000},
        {"aweme_id": "", "title": "blank2", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000},
    ]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    assert summary["content_count"] == 2
    with _open_duckdb(summary) as conn:
        titles = [r[0] for r in conn.execute("SELECT title FROM contents ORDER BY title").fetchall()]
    assert titles == ["blank1", "blank2"]


# Type handling and raw_json (cases 25-26)


def test_int_and_string_interaction_types_both_parsed(tmp_path):
    contents = [
        {"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000, "liked_count": 7, "comment_count": "3", "collected_count": 2, "share_count": "1"},
    ]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        like, comment, collect, share = conn.execute(
            "SELECT like_count, comment_count, collect_count, share_count FROM contents"
        ).fetchone()
    assert like == 7
    assert comment == 3
    assert collect == 2
    assert share == 1


def test_comment_like_count_int_and_string(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000}]
    comments = [
        {"comment_id": "i", "aweme_id": "7400000000000000001", "content": "int_like", "user_id": "u", "like_count": 4, "create_time": 1780000001, "parent_comment_id": "0", "last_modify_ts": 1780000000001},
        {"comment_id": "s", "aweme_id": "7400000000000000001", "content": "str_like", "user_id": "u", "like_count": "9", "create_time": 1780000001, "parent_comment_id": "0", "last_modify_ts": 1780000000001},
    ]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, comments)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        likes = dict(conn.execute("SELECT comment_id, like_count FROM comments").fetchall())
    assert likes["i"] == 4
    assert likes["s"] == 9


def test_raw_json_preserves_original_fields(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "Cursor", "aweme_type": "0", "user_id": "u", "nickname": "nick", "create_time": 1780000000, "last_modify_ts": 1780000000000, "liked_count": "100", "comment_count": "10", "collected_count": "5", "share_count": "2", "aweme_url": "https://www.douyin.com/video/7400000000000000001"}]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        raw = conn.execute("SELECT raw_json FROM contents").fetchone()[0]
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    assert parsed["aweme_id"] == "7400000000000000001"
    assert parsed["aweme_type"] == "0"
    assert parsed["source_keyword"] == "Cursor"
    assert parsed["liked_count"] == "100"


def test_content_type_known_value_mapped_and_unknown_fallback(tmp_path):
    contents = [
        {"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000},
        {"aweme_id": "7400000000000000002", "title": "t", "desc": "d", "source_keyword": "k", "aweme_type": "68", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000},
    ]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        types = dict(conn.execute("SELECT content_id, content_type FROM contents").fetchall())
    assert types["7400000000000000001"] == "video"
    assert types["7400000000000000002"] == "douyin_aweme_68"


# Compatibility (cases 27-30)


def test_xhs_normalization_still_works_unchanged(tmp_path):
    dataset_service, _, _ = _services(tmp_path)
    dataset = dataset_service.create_dataset(name="XHS regression", platforms=["xhs"], keywords=["AI"])
    raw_dir = tmp_path / "datasets" / dataset.dataset_id / "raw"
    _write_jsonl(
        raw_dir / "xhs_contents.jsonl",
        [{"note_id": "n1", "title": "AI post", "desc": "d", "source_keyword": "AI", "nickname": "a", "user_id": "u", "liked_count": "10", "collected_count": "2", "comment_count": "1", "share_count": "0"}],
    )
    _write_jsonl(
        raw_dir / "xhs_comments.jsonl",
        [{"note_id": "n1", "comment_id": "c1", "content": "comment", "user_id": "cu", "like_count": "5", "parent_comment_id": "0"}],
    )
    normalizer = DatasetNormalizer(Storage(McpConfig(home=tmp_path, browser_mode="persistent_context", cdp_endpoint=None, max_concurrent_tasks=1, default_timeout_seconds=300)))
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    assert summary["content_count"] == 1
    assert summary["comment_count"] == 1
    with _open_duckdb(summary) as conn:
        platform, parent = conn.execute("SELECT platform, parent_comment_id FROM comments").fetchone()
    assert platform == "xhs"
    assert parent == "0"


def test_unknown_platform_raises_explicit_error_not_silent_xhs(tmp_path):
    config = McpConfig(
        home=tmp_path,
        browser_mode="persistent_context",
        cdp_endpoint=None,
        max_concurrent_tasks=1,
        default_timeout_seconds=300,
    )
    from mediacrawler_mcp.storage import Storage as _Storage
    storage = _Storage(config)
    storage.initialize()
    dataset_id = "ds_bilibili_unknown_test"
    dataset_dir = tmp_path / "datasets" / dataset_id
    raw_dir = dataset_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(raw_dir / "bilibili_contents.jsonl", [{}])
    manifest = {
        "dataset_id": dataset_id,
        "name": "unknown",
        "description": None,
        "status": "created",
        "platforms": ["bilibili"],
        "keywords": ["x"],
        "options": {},
        "dataset_dir": str(dataset_dir),
        "created_at": "2026-07-12T00:00:00+00:00",
        "updated_at": "2026-07-12T00:00:00+00:00",
    }
    manifest_path = dataset_dir / "dataset.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    from mediacrawler_mcp.models import Dataset
    storage.upsert_dataset(Dataset(
        dataset_id=dataset_id,
        name="unknown",
        description=None,
        status="created",
        platforms=["bilibili"],
        keywords=["x"],
        options={},
        dataset_dir=str(dataset_dir),
        created_at="2026-07-12T00:00:00+00:00",
        updated_at="2026-07-12T00:00:00+00:00",
    ))
    normalizer = DatasetNormalizer(storage)
    with pytest.raises(McpAppError) as exc_info:
        normalizer.normalize_dataset(dataset_id)
    assert exc_info.value.code == ErrorCode.NORMALIZE_FAILED
    assert "bilibili" in str(exc_info.value)


def test_douyin_bundle_can_be_directly_normalized(tmp_path):
    contents, comments = _one_content_two_level_comments()
    contents_path = tmp_path / "src" / "douyin_contents.jsonl"
    comments_path = tmp_path / "src" / "douyin_comments.jsonl"
    _write_jsonl(contents_path, contents)
    _write_jsonl(comments_path, comments)
    exporter = DatasetBundleExporter()
    bundle_result = exporter.export_douyin_bundle(
        name="Douyin bundle normalize journey",
        output_dir=tmp_path / "inbox",
        keywords=["Cursor"],
        contents_path=contents_path,
 comments_path=comments_path,
        run_id="run_test_001",
        collection_started_at="2026-07-12T00:00:00+00:00",
    )
    _, importer, normalizer = _services(tmp_path)
    importer.register_dataset(bundle_result["dataset_dir"], import_mode="copy")
    dataset_dir = Path(bundle_result["dataset_dir"]).name
    summary = normalizer.normalize_dataset(dataset_dir)
    assert summary["content_count"] == 1
    assert summary["comment_count"] == 3
    with _open_duckdb(summary) as conn:
        first_level = conn.execute("SELECT COUNT(*) FROM comments WHERE parent_comment_id IS NULL").fetchone()[0]
        second_level = conn.execute("SELECT COUNT(*) FROM comments WHERE parent_comment_id IS NOT NULL").fetchone()[0]
        source_keywords = conn.execute("SELECT DISTINCT source_keyword FROM comments").fetchall()
    assert first_level == 1
    assert second_level == 2
    assert {r[0] for r in source_keywords} == {"Cursor"}
    assert summary["orphan_comment_count"] == 0
    assert summary["reply_lineage_anomaly_count"] == 0


def test_contents_only_douyin_dataset_can_normalize(tmp_path):
    contents = [{"aweme_id": "7400000000000000001", "title": "t", "desc": "d", "source_keyword": "Cursor", "aweme_type": "0", "user_id": "u", "create_time": 1780000000, "last_modify_ts": 1780000000000}]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, None)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    assert summary["content_count"] == 1
    assert summary["comment_count"] == 0
    assert summary["orphan_comment_count"] == 0


def test_douyin_no_spare_columns_exposure_duration_hashtags(tmp_path):
    contents, comments = _one_content_two_level_comments()
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, comments)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    with _open_duckdb(summary) as conn:
        content_cols = [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'contents'").fetchall()]
        comment_cols = [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'comments'").fetchall()]
    forbidden = {"exposure_count", "duration_ms", "hashtags", "challenges"}
    assert not (forbidden & set(content_cols))
    assert not (forbidden & set(comment_cols))


def test_fixture_files_normalize_end_to_end(tmp_path):
    contents = [json.loads(line) for line in (FIXTURE_DIR / "contents.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    comments = [json.loads(line) for line in (FIXTURE_DIR / "comments.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    dataset, normalizer, _ = _make_douyin_dataset(tmp_path, contents, comments)
    summary = normalizer.normalize_dataset(dataset.dataset_id)
    assert summary["content_count"] == 2
    assert summary["comment_count"] == 3
    with _open_duckdb(summary) as conn:
        first_level = conn.execute("SELECT COUNT(*) FROM comments WHERE parent_comment_id IS NULL").fetchone()[0]
        second_level = conn.execute("SELECT COUNT(*) FROM comments WHERE parent_comment_id IS NOT NULL").fetchone()[0]
    assert first_level == 1
    assert second_level == 2
    assert summary["reply_lineage_anomaly_count"] == 0
    assert summary["orphan_comment_count"] == 0
