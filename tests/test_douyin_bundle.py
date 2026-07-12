import json
from pathlib import Path

import pytest

from mediacrawler_mcp.dataset_bundle_exporter import DatasetBundleExporter
from mediacrawler_mcp.dataset_importer import RAW_FILE_NAMES
from mediacrawler_mcp.errors import ErrorCode, McpAppError


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "douyin"

BANNED_SENSITIVE_FIELDS = {
    "sec_uid",
    "xsec_token",
    "user_signature",
    "cookie",
    "token",
    "device",
    "verifyfp",
    "mstoken",
    "a_bogus",
    "signature",
    "ip_location",
}

BANNED_SENSITIVE_SUBSTRINGS = (
    "douyinvod",
    "byteimg",
    "xhscdn",
    "sec_uid",
    "xsec_token",
    "user_signature",
    "cookie",
    "verifyfp",
    "mstoken",
    "a_bogus",
    "signature",
    "ip_location",
    "token",
    "device",
)


def _load_fixture(name: str) -> list[dict]:
    rows = []
    for line in (FIXTURE_DIR / name).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _make_bundle(tmp_path: Path, *, contents_name: str = "contents.jsonl", comments_name: str | None = "comments.jsonl", **kwargs):
    contents_path = tmp_path / "inputs" / contents_name
    contents_path.parent.mkdir(parents=True, exist_ok=True)
    contents_path.write_text((FIXTURE_DIR / "contents.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    comments_path = None
    if comments_name is not None:
        comments_path = tmp_path / "inputs" / comments_name
        comments_path.write_text((FIXTURE_DIR / "comments.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    output_dir = tmp_path / "inbox"
    return DatasetBundleExporter().export_douyin_bundle(
        name="Douyin smoke fixture",
        output_dir=output_dir,
        keywords=["Cursor编辑器"],
        contents_path=contents_path,
        comments_path=comments_path,
        **kwargs,
    )


def test_douyin_bundle_run_isolated_layout(tmp_path):
    result = _make_bundle(tmp_path, run_id="run_20260711_175221_0b7224", collection_started_at="2026-07-11T17:52:21+00:00")
    bundle_dir = Path(result["dataset_dir"])
    manifest = json.loads((bundle_dir / "dataset.json").read_text(encoding="utf-8"))

    assert manifest["platforms"] == ["douyin"]
    assert manifest["options"]["output_layout"] == "run_isolated"
    assert manifest["options"]["run_id"] == "run_20260711_175221_0b7224"
    assert manifest["options"]["collection_started_at"] == "2026-07-11T17:52:21+00:00"


def test_douyin_bundle_raw_file_names(tmp_path):
    result = _make_bundle(tmp_path)
    bundle_dir = Path(result["dataset_dir"])
    assert (bundle_dir / "raw" / "douyin_contents.jsonl").exists()
    assert (bundle_dir / "raw" / "douyin_comments.jsonl").exists()
    assert result["raw_files"]["contents"]["platform"] == "douyin"


def test_douyin_bundle_raw_record_counts(tmp_path):
    result = _make_bundle(tmp_path)
    assert result["metrics"] == {"content_count": 2, "comment_count": 3}


def test_douyin_bundle_contents_only_when_comments_missing(tmp_path):
    result = _make_bundle(tmp_path, comments_name=None)
    bundle_dir = Path(result["dataset_dir"])
    assert (bundle_dir / "raw" / "douyin_contents.jsonl").exists()
    assert not (bundle_dir / "raw" / "douyin_comments.jsonl").exists()
    assert result["metrics"]["content_count"] == 2
    assert result["metrics"]["comment_count"] == 0
    assert any("contents only" in w for w in result["warnings"])


def test_douyin_bundle_manifest_records_run_id_and_source_paths(tmp_path):
    result = _make_bundle(
        tmp_path,
        run_id="run_abc",
        source_contents_path="data/smoke_dy_clitest/douyin/run_abc/jsonl/search_contents.jsonl",
        source_comments_path="data/smoke_dy_clitest/douyin/run_abc/jsonl/search_comments.jsonl",
    )
    manifest = json.loads((Path(result["dataset_dir"]) / "dataset.json").read_text(encoding="utf-8"))
    assert manifest["options"]["run_id"] == "run_abc"
    assert manifest["options"]["contents_source"] == "data/smoke_dy_clitest/douyin/run_abc/jsonl/search_contents.jsonl"
    assert manifest["options"]["comments_source"] == "data/smoke_dy_clitest/douyin/run_abc/jsonl/search_comments.jsonl"


def test_douyin_bundle_raw_comment_not_supplemented_with_source_keyword(tmp_path):
    result = _make_bundle(tmp_path)
    bundle_dir = Path(result["dataset_dir"])
    comment_rows = [
        json.loads(line)
        for line in (bundle_dir / "raw" / "douyin_comments.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    for row in comment_rows:
        assert "source_keyword" not in row
    assert any("source_keyword" in w for w in result["warnings"])


def test_douyin_bundle_preserves_int_and_string_interaction_types(tmp_path):
    result = _make_bundle(tmp_path)
    bundle_dir = Path(result["dataset_dir"])
    contents = [
        json.loads(line)
        for line in (bundle_dir / "raw" / "douyin_contents.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    comments = [
        json.loads(line)
        for line in (bundle_dir / "raw" / "douyin_comments.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    for c in contents:
        assert isinstance(c["liked_count"], str)
        assert isinstance(c["comment_count"], str)
    assert isinstance(comments[0]["like_count"], int)


def test_douyin_bundle_second_level_parent_comment_id_structure(tmp_path):
    result = _make_bundle(tmp_path)
    bundle_dir = Path(result["dataset_dir"])
    comments = [
        json.loads(line)
        for line in (bundle_dir / "raw" / "douyin_comments.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    roots = [c for c in comments if str(c["parent_comment_id"]) == "0"]
    replies = [c for c in comments if str(c["parent_comment_id"]) != "0"]
    assert len(roots) == 1
    assert len(replies) == 2
    for reply in replies:
        assert reply["parent_comment_id"] == roots[0]["comment_id"]
        assert "reply_id" not in reply
        assert "root_comment_id" not in reply
        assert "reply_to_comment_id" not in reply


def test_douyin_bundle_refuses_mismatched_aweme_linkage(tmp_path):
    contents_path = tmp_path / "inputs" / "contents.jsonl"
    contents_path.parent.mkdir(parents=True, exist_ok=True)
    contents_path.write_text(
        json.dumps({"aweme_id": "7400000000000000001", "title": "x"}, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    comments_path = tmp_path / "inputs" / "comments.jsonl"
    comments_path.write_text(
        json.dumps({"comment_id": "c0001", "aweme_id": "7400000000000000999", "parent_comment_id": "0"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(McpAppError) as exc_info:
        DatasetBundleExporter().export_douyin_bundle(
            name="mismatched",
            output_dir=tmp_path / "inbox",
            keywords=["k"],
            contents_path=contents_path,
            comments_path=comments_path,
            dataset_id="dy_mismatch",
        )
    assert exc_info.value.code == ErrorCode.INVALID_ARGUMENT


def test_douyin_bundle_rejects_merged_multiple_runs(tmp_path):
    _make_bundle(tmp_path, dataset_id="dy_unique", run_id="run_one")
    with pytest.raises(McpAppError) as exc_info:
        _make_bundle(tmp_path, dataset_id="dy_unique", run_id="run_two")
    assert exc_info.value.code == ErrorCode.INVALID_ARGUMENT


def test_douyin_bundle_capability_block_documents_source_keyword_inheritance(tmp_path):
    result = _make_bundle(tmp_path)
    manifest = json.loads((Path(result["dataset_dir"]) / "dataset.json").read_text(encoding="utf-8"))
    cap = manifest["capability"]
    assert cap["platform"] == "douyin"
    assert cap["source_keyword_in_contents"] is True
    assert cap["source_keyword_in_comments"] is False
    assert cap["source_keyword_comments_inherit"] == "aweme_id"
    assert cap["second_level_parent_field"] == "parent_comment_id"
    assert cap["second_level_reply_to_fields_present"] is False
    assert cap["comments_source_keyword"] == "absent_in_raw"
    assert cap["comments_source_keyword_derivation"] == "inherit_from_parent_by_aweme_id_in_normalization"
    assert cap["reply_model"] == "root_and_second_level_only"
    assert cap["reply_to_reply_chain"] == "unavailable"


def test_douyin_fixture_safety_no_sensitive_fields():
    offending = []
    for fixture in FIXTURE_DIR.iterdir():
        if not fixture.is_file():
            continue
        for line_no, line in enumerate(fixture.read_text(encoding="utf-8").splitlines(), start=1):
            lower = line.lower()
            for banned in BANNED_SENSITIVE_SUBSTRINGS:
                if banned in lower:
                    offending.append(f"{fixture.name}:{line_no}: '{banned}'")
        for row in (json.loads(l) for l in fixture.read_text(encoding="utf-8").splitlines() if l.strip()):
            for field in BANNED_SENSITIVE_FIELDS:
                if field in row:
                    offending.append(f"{fixture.name}: field '{field}' present")
    assert not offending, "Sensitive content found in douyin fixture: " + ", ".join(offending)


def test_douyin_fixture_linkage_consistent():
    contents = _load_fixture("contents.jsonl")
    comments = _load_fixture("comments.jsonl")
    content_awemes = {c["aweme_id"] for c in contents}
    root_cids = {c["comment_id"] for c in comments if str(c["parent_comment_id"]) == "0"}
    for comment in comments:
        assert comment["aweme_id"] in content_awemes, f"comment aweme {comment['aweme_id']} not in contents"
        if str(comment["parent_comment_id"]) != "0":
            assert comment["parent_comment_id"] in root_cids, "second-level parent not a root comment"


def test_xhs_bundle_still_passes_regression(tmp_path):
    jsonl_dir = tmp_path / "data" / "xhs" / "jsonl"
    jsonl_dir.mkdir(parents=True)
    (jsonl_dir / "search_contents_2026-07-02.jsonl").write_text(
        json.dumps({"note_id": "n1", "title": "xhs", "source_keyword": "k"}, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (jsonl_dir / "search_comments_2026-07-02.jsonl").write_text(
        json.dumps({"note_id": "n1", "comment_id": "c1"}, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    result = DatasetBundleExporter().export_xhs_bundle(
        name="xhs reg",
        output_dir=tmp_path / "inbox",
        keywords=["k"],
        data_root=tmp_path / "data",
        dataset_id="xhs_reg",
    )
    assert result["metrics"]["content_count"] == 1
    assert result["metrics"]["comment_count"] == 1
    assert RAW_FILE_NAMES["xhs"]["contents"] == "xhs_contents.jsonl"
