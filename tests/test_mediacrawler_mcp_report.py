import json
from pathlib import Path

import pytest

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.dataset_service import DatasetService
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.normalizer import DatasetNormalizer
from mediacrawler_mcp.report_service import ReportService
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.utils import strip_sensitive_url_params


def _services(tmp_path):
    config = McpConfig(
        home=tmp_path,
        browser_mode="persistent_context",
        cdp_endpoint=None,
        max_concurrent_tasks=1,
        default_timeout_seconds=300,
    )
    storage = Storage(config)
    return DatasetService(config, storage), DatasetNormalizer(storage), ReportService(storage)


def _write_jsonl(path: Path, rows):
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _normalized_dataset(tmp_path):
    dataset_service, normalizer, report_service = _services(tmp_path)
    dataset = dataset_service.create_dataset(
        name="小红书口碑报告",
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
                "desc": "私信领取资料包",
                "nickname": "作者A",
                "user_id": "u1",
                "liked_count": "100",
                "collected_count": "20",
                "comment_count": "10",
                "share_count": "5",
                "source_keyword": "程序员接单",
            },
            {
                "note_id": "n2",
                "note_url": "https://www.xiaohongshu.com/explore/n2",
                "title": "AI编程副业",
                "desc": "真实反馈",
                "nickname": "作者B",
                "user_id": "u2",
                "liked_count": "50",
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
                "content": "最怕客户不付款",
                "nickname": "用户A",
                "user_id": "cu1",
                "like_count": "9",
                "source_keyword": "程序员接单",
            },
            {
                "note_id": "n2",
                "comment_id": "c2",
                "content": "担心接不到单",
                "nickname": "用户B",
                "user_id": "cu2",
                "like_count": "3",
                "source_keyword": "AI编程副业",
            },
        ],
    )
    normalizer.normalize_dataset(dataset.dataset_id)
    return dataset, report_service


def test_generate_report_writes_markdown_html_summary_and_report_row(tmp_path):
    dataset, report_service = _normalized_dataset(tmp_path)

    result = report_service.generate_report(dataset.dataset_id, top_n=10)

    assert result["report_id"].startswith("report_")
    assert result["summary"]["content_count"] == 2
    assert result["summary"]["comment_count"] == 2
    assert result["summary"]["top_keywords"]
    assert "程序员接单" not in result["summary"]["top_keywords"]
    assert "AI编程副业" not in result["summary"]["top_keywords"]

    report_md = Path(result["report_md_path"])
    report_html = Path(result["report_html_path"])
    summary_json = Path(result["summary_json_path"])
    assert report_md.exists()
    assert report_html.exists()
    assert summary_json.exists()
    assert "高互动内容" in report_md.read_text(encoding="utf-8")
    assert "<html" in report_html.read_text(encoding="utf-8")

    summary = json.loads(summary_json.read_text(encoding="utf-8"))
    assert summary["metrics"]["author_count"] == 2
    assert summary["metrics"]["comment_user_count"] == 2
    assert summary["keyword_distribution"] == {"程序员接单": 1, "AI编程副业": 1}
    assert summary["content_word_freq"]
    assert result["summary"]["top_keywords"] == list(summary["content_word_freq"].keys())[:10]
    assert summary["top_contents"][0]["content_id"] == "n1"
    assert "publish_datetime" in summary["top_contents"][0]
    assert "tags" in summary["top_contents"][0]
    assert summary["top_comments"][0]["comment_id"] == "c1"
    assert "最怕客户不付款" not in summary["comment_word_freq"]
    assert {"客户", "付款"} & set(summary["comment_word_freq"])
    assert summary["ad_candidates"][0]["content_id"] == "n1"

    fetched = report_service.get_report(dataset.dataset_id)
    assert fetched["report_id"] == result["report_id"]
    assert fetched["report_md_path"] == str(report_md)


def test_get_report_can_fetch_specific_report(tmp_path):
    dataset, report_service = _normalized_dataset(tmp_path)

    result = report_service.generate_report(dataset.dataset_id, report_type="topic_research")

    fetched = report_service.get_report(dataset.dataset_id, result["report_id"])

    assert fetched["report_id"] == result["report_id"]
    assert fetched["report_type"] == "topic_research"


def test_generate_report_requires_normalized_dataset(tmp_path):
    dataset_service, _, report_service = _services(tmp_path)
    dataset = dataset_service.create_dataset(
        name="未标准化",
        platforms=["xhs"],
        keywords=["程序员接单"],
    )

    with pytest.raises(McpAppError) as exc_info:
        report_service.generate_report(dataset.dataset_id)

    assert exc_info.value.code == ErrorCode.REPORT_FAILED


def test_get_report_returns_structured_error_for_missing_report(tmp_path):
    dataset_service, _, report_service = _services(tmp_path)
    dataset = dataset_service.create_dataset(
        name="无报告",
        platforms=["xhs"],
        keywords=["程序员接单"],
    )

    with pytest.raises(McpAppError) as exc_info:
        report_service.get_report(dataset.dataset_id)

    assert exc_info.value.code == ErrorCode.REPORT_NOT_FOUND


def test_get_report_returns_dataset_not_found(tmp_path):
    _, _, report_service = _services(tmp_path)

    with pytest.raises(McpAppError) as exc_info:
        report_service.get_report("missing")

    assert exc_info.value.code == ErrorCode.DATASET_NOT_FOUND


def test_strip_sensitive_url_params_preserves_path_strips_tokens():
    # XHS URL with xsec_token query param
    url = "https://www.xiaohongshu.com/explore/abc123?xsec_token=AB8V35PN2b_eH801FzmQy7lewWVnDICAMN&xsec_source=pc_search"
    result = strip_sensitive_url_params(url)
    assert "xsec_token" not in result
    assert "xsec_source=pc_search" in result
    assert result.startswith("https://www.xiaohongshu.com/explore/abc123")

    # Douyin URL with no query params stays unchanged
    assert strip_sensitive_url_params("https://www.douyin.com/video/123") == "https://www.douyin.com/video/123"

    # None passthrough
    assert strip_sensitive_url_params(None) is None

    # Non-http scheme is returned as-is (callers reject separately)
    assert strip_sensitive_url_params("javascript:alert(1)") == "javascript:alert(1)"

    # Multiple sensitive params all removed
    url2 = "https://example.test/page?token=secret&msToken=xyz&a_bogus=abc&keep=this"
    result2 = strip_sensitive_url_params(url2)
    assert "token=" not in result2
    assert "msToken=" not in result2
    assert "a_bogus=" not in result2
    assert "keep=this" in result2


def test_report_strips_xsec_token_from_all_output_formats(tmp_path):
    dataset_service, normalizer, report_service = _services(tmp_path)
    dataset = dataset_service.create_dataset(
        name="URL安全报告",
        platforms=["xhs"],
        keywords=["测试"],
    )
    raw_dir = tmp_path / "datasets" / dataset.dataset_id / "raw"
    _write_jsonl(
        raw_dir / "xhs_contents.jsonl",
        [
            {
                "note_id": "n1",
                "note_url": "https://www.xiaohongshu.com/explore/n1?xsec_token=SECRET_TOKEN_VALUE&xsec_source=pc_search",
                "title": "私信领取资料包",
                "desc": "广告内容",
                "nickname": "作者A",
                "user_id": "u1",
                "liked_count": "100",
                "collected_count": "20",
                "comment_count": "10",
                "share_count": "5",
                "source_keyword": "测试",
            },
        ],
    )
    _write_jsonl(
        raw_dir / "xhs_comments.jsonl",
        [
            {
                "note_id": "n1",
                "comment_id": "c1",
                "content": "评论",
                "nickname": "用户A",
                "user_id": "cu1",
                "like_count": "9",
                "source_keyword": "测试",
            },
        ],
    )
    normalizer.normalize_dataset(dataset.dataset_id)

    result = report_service.generate_report(dataset.dataset_id, top_n=10)

    summary = json.loads(Path(result["summary_json_path"]).read_text(encoding="utf-8"))
    md_text = Path(result["report_md_path"]).read_text(encoding="utf-8")
    html_text = Path(result["report_html_path"]).read_text(encoding="utf-8")

    # Check URL fields specifically (the test tmpdir name itself contains xsec_token)
    for item in summary.get("top_contents", []) + summary.get("ad_candidates", []):
        url = item.get("url", "")
        assert "xsec_token" not in url, f"xsec_token in top_contents/ad_candidates url: {url}"
        assert "SECRET_TOKEN_VALUE" not in url, f"secret value in url: {url}"

    # Markdown and HTML must not leak the secret token value
    assert "SECRET_TOKEN_VALUE" not in md_text
    assert "SECRET_TOKEN_VALUE" not in html_text
    # The token query param name must not appear as a URL query key
    assert "xsec_token=" not in md_text
    assert "xsec_token=" not in html_text

    # Stable path preserved
    assert "https://www.xiaohongshu.com/explore/n1" in json.dumps(summary["top_contents"])
    assert "xsec_source=pc_search" in json.dumps(summary)
