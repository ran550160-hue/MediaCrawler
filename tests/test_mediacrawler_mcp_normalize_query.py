import json

import duckdb
import pytest

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.dataset_service import DatasetService
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.normalizer import DatasetNormalizer
from mediacrawler_mcp.query_engine import QueryEngine
from mediacrawler_mcp.storage import Storage


def _services(tmp_path):
    config = McpConfig(
        home=tmp_path,
        browser_mode="persistent_context",
        cdp_endpoint=None,
        max_concurrent_tasks=1,
        default_timeout_seconds=300,
    )
    storage = Storage(config)
    dataset_service = DatasetService(config, storage)
    return dataset_service, DatasetNormalizer(storage), QueryEngine(storage)


def _write_jsonl(path, rows):
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _dataset_with_raw(tmp_path):
    dataset_service, normalizer, query_engine = _services(tmp_path)
    dataset = dataset_service.create_dataset(
        name="小红书接单调研",
        platforms=["xhs"],
        keywords=["程序员接单", "AI编程副业"],
    )
    raw_dir = tmp_path / "datasets" / dataset.dataset_id / "raw"
    _write_jsonl(
        raw_dir / "xhs_contents.jsonl",
        [
            {
                "note_id": "n1",
                "note_url": "https://www.xiaohongshu.com/explore/n1",
                "title": "程序员接单避坑",
                "desc": "报价和验收都要提前说清楚",
                "nickname": "作者A",
                "user_id": "u1",
                "liked_count": "1,200",
                "collected_count": "3",
                "comment_count": "4",
                "share_count": "5",
                "source_keyword": "程序员接单",
                "publish_time": "2026-06-28",
            },
            {
                "note_id": "n1",
                "note_url": "https://www.xiaohongshu.com/explore/n1",
                "title": "程序员接单避坑 duplicate",
                "liked_count": "2",
                "source_keyword": "程序员接单",
            },
            {
                "note_id": "n2",
                "note_url": "https://www.xiaohongshu.com/explore/n2",
                "title": "AI编程副业真实反馈",
                "desc": "很多人担心接不到单",
                "liked_count": "1.5万",
                "collected_count": "",
                "comment_count": None,
                "share_count": "bad",
                "source_keyword": "AI编程副业",
            },
        ],
    )
    _write_jsonl(
        raw_dir / "xhs_comments.jsonl",
        [
            {
                "note_id": "n1",
                "comment_id": "c1",
                "content": "最怕接单后客户不付款",
                "nickname": "用户A",
                "user_id": "cu1",
                "like_count": "42",
                "source_keyword": "程序员接单",
            },
            {
                "note_id": "n1",
                "comment_id": "c1",
                "content": "重复低赞评论",
                "like_count": "1",
                "source_keyword": "程序员接单",
            },
            {
                "note_id": "n2",
                "comment_id": "c2",
                "content": "担心没有案例接不到单",
                "like_count": "5",
                "source_keyword": "AI编程副业",
            },
        ],
    )
    return dataset, normalizer, query_engine


def test_normalize_dataset_writes_duckdb_tables_and_counts(tmp_path):
    dataset, normalizer, _ = _dataset_with_raw(tmp_path)

    summary = normalizer.normalize_dataset(dataset.dataset_id)

    assert summary["content_count"] == 2
    assert summary["comment_count"] == 2
    assert summary["duckdb_path"].endswith("analysis.duckdb")

    with duckdb.connect(summary["duckdb_path"], read_only=True) as conn:
        contents = conn.execute(
            "SELECT content_id, source_keyword, like_count, engagement_count FROM contents ORDER BY content_id"
        ).fetchall()
        comments = conn.execute(
            "SELECT comment_id, comment_text, like_count FROM comments ORDER BY comment_id"
        ).fetchall()

    assert contents == [
        ("n1", "程序员接单", 1200, 1212),
        ("n2", "AI编程副业", 15000, 15000),
    ]
    assert comments == [
        ("c1", "最怕接单后客户不付款", 42),
        ("c2", "担心没有案例接不到单", 5),
    ]


def test_normalize_dataset_supports_comments_only(tmp_path):
    dataset_service, normalizer, _ = _services(tmp_path)
    dataset = dataset_service.create_dataset(
        name="评论-only",
        platforms=["xhs"],
        keywords=["程序员接单"],
    )
    raw_dir = tmp_path / "datasets" / dataset.dataset_id / "raw"
    _write_jsonl(
        raw_dir / "xhs_comments.jsonl",
        [{"note_id": "n1", "comment_id": "c1", "content": "只分析评论", "source_keyword": "程序员接单"}],
    )

    summary = normalizer.normalize_dataset(dataset.dataset_id)

    assert summary["content_count"] == 0
    assert summary["comment_count"] == 1


def test_normalize_dataset_errors_when_raw_files_missing(tmp_path):
    dataset_service, normalizer, _ = _services(tmp_path)
    dataset = dataset_service.create_dataset(
        name="缺文件",
        platforms=["xhs"],
        keywords=["程序员接单"],
    )

    with pytest.raises(McpAppError) as exc_info:
        normalizer.normalize_dataset(dataset.dataset_id)

    assert exc_info.value.code == ErrorCode.NORMALIZE_FAILED


def test_query_dataset_filters_comments_by_query_keyword_and_sort(tmp_path):
    dataset, normalizer, query_engine = _dataset_with_raw(tmp_path)
    normalizer.normalize_dataset(dataset.dataset_id)

    results = query_engine.query_dataset(
        dataset_id=dataset.dataset_id,
        target="comments",
        query="担心 接不到单",
        platform="xhs",
        source_keyword="AI编程副业",
        limit=5,
    )

    assert len(results) == 1
    assert results[0]["comment_id"] == "c2"
    assert results[0]["text"] == "担心没有案例接不到单"
    assert results[0]["source_keyword"] == "AI编程副业"


def test_query_dataset_supports_offset_pagination(tmp_path):
    dataset, normalizer, query_engine = _dataset_with_raw(tmp_path)
    normalizer.normalize_dataset(dataset.dataset_id)

    results = query_engine.query_dataset(
        dataset_id=dataset.dataset_id,
        target="comments",
        query="",
        limit=1,
        offset=1,
    )

    assert len(results) == 1
    assert results[0]["comment_id"] == "c2"


def test_query_dataset_can_query_contents(tmp_path):
    dataset, normalizer, query_engine = _dataset_with_raw(tmp_path)
    normalizer.normalize_dataset(dataset.dataset_id)

    results = query_engine.query_dataset(
        dataset_id=dataset.dataset_id,
        target="contents",
        query="接单 避坑",
        sort_by="engagement_count",
    )

    assert len(results) == 1
    assert results[0]["content_id"] == "n1"
    assert "程序员接单避坑" in results[0]["text"]
    assert results[0]["url"] == "https://www.xiaohongshu.com/explore/n1"


def test_query_dataset_requires_normalized_database(tmp_path):
    dataset_service, _, query_engine = _services(tmp_path)
    dataset = dataset_service.create_dataset(
        name="未标准化",
        platforms=["xhs"],
        keywords=["程序员接单"],
    )

    with pytest.raises(McpAppError) as exc_info:
        query_engine.query_dataset(dataset.dataset_id, query="接单")

    assert exc_info.value.code == ErrorCode.QUERY_FAILED


def test_query_dataset_validates_target_and_sort(tmp_path):
    dataset, normalizer, query_engine = _dataset_with_raw(tmp_path)
    normalizer.normalize_dataset(dataset.dataset_id)

    with pytest.raises(McpAppError) as target_error:
        query_engine.query_dataset(dataset.dataset_id, query="接单", target="users")
    assert target_error.value.code == ErrorCode.INVALID_ARGUMENT

    with pytest.raises(McpAppError) as sort_error:
        query_engine.query_dataset(dataset.dataset_id, query="接单", target="comments", sort_by="engagement_count")
    assert sort_error.value.code == ErrorCode.INVALID_ARGUMENT
