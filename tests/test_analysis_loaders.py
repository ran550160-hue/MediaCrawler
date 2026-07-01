import json
import os
import time

import pandas as pd
import pytest

from analysis.loaders import (
    AnalysisDataError,
    discover_latest_data_file,
    load_dataframe,
    resolve_input_files,
)


def test_load_dataframe_supports_jsonl_json_and_csv(tmp_path):
    jsonl_path = tmp_path / "items.jsonl"
    jsonl_path.write_text(
        json.dumps({"id": "1", "name": "jsonl"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assert load_dataframe(jsonl_path).to_dict("records") == [{"id": "1", "name": "jsonl"}]

    json_path = tmp_path / "items.json"
    json_path.write_text(json.dumps([{"id": "2", "name": "json"}]), encoding="utf-8")
    assert load_dataframe(json_path).to_dict("records") == [{"id": "2", "name": "json"}]

    csv_path = tmp_path / "items.csv"
    pd.DataFrame([{"id": "3", "name": "csv"}]).to_csv(csv_path, index=False)
    assert load_dataframe(csv_path).to_dict("records") == [{"id": "3", "name": "csv"}]


def test_load_dataframe_rejects_unknown_suffix(tmp_path):
    path = tmp_path / "items.txt"
    path.write_text("nope", encoding="utf-8")

    with pytest.raises(AnalysisDataError):
        load_dataframe(path)


def test_discover_latest_data_file_uses_mtime_then_name(tmp_path):
    data_root = tmp_path / "data"
    jsonl_dir = data_root / "xhs" / "jsonl"
    jsonl_dir.mkdir(parents=True)

    old_file = jsonl_dir / "search_contents_2024-01-01.jsonl"
    new_file = jsonl_dir / "search_contents_2024-01-02.jsonl"
    old_file.write_text("{}", encoding="utf-8")
    new_file.write_text("{}", encoding="utf-8")
    now = time.time()
    os.utime(old_file, (now - 100, now - 100))
    os.utime(new_file, (now, now))

    assert discover_latest_data_file("xhs", "search", "contents", data_root) == new_file


def test_resolve_input_files_prefers_explicit_paths(tmp_path):
    explicit_contents = tmp_path / "custom_contents.jsonl"
    explicit_comments = tmp_path / "custom_comments.jsonl"
    explicit_contents.write_text("{}", encoding="utf-8")
    explicit_comments.write_text("{}", encoding="utf-8")

    contents, comments = resolve_input_files(
        platform="dy",
        crawler_type="search",
        contents=explicit_contents,
        comments=explicit_comments,
        data_root=tmp_path / "missing",
    )

    assert contents == explicit_contents
    assert comments == explicit_comments


def test_dy_auto_discovery_uses_douyin_data_directory(tmp_path):
    data_root = tmp_path / "data"
    douyin_dir = data_root / "douyin" / "jsonl"
    douyin_dir.mkdir(parents=True)
    contents_file = douyin_dir / "search_contents_2026-06-27.jsonl"
    comments_file = douyin_dir / "search_comments_2026-06-27.jsonl"
    contents_file.write_text("{}", encoding="utf-8")
    comments_file.write_text("{}", encoding="utf-8")

    contents, comments = resolve_input_files(
        platform="dy",
        crawler_type="search",
        data_root=data_root,
    )

    assert contents == contents_file
    assert comments == comments_file
