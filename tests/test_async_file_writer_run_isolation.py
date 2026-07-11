import asyncio
import json
from pathlib import Path

import config

from tools.async_file_writer import AsyncFileWriter


def _configure(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVE_DATA_PATH", str(tmp_path))
    monkeypatch.setattr(config, "SAVE_DATA_RUN_ID", "")
    monkeypatch.setattr(config, "SAVE_DATA_SHARED_OUTPUT", False)
    monkeypatch.setattr(config, "KEYWORDS", "same,keyword")
    monkeypatch.setattr(config, "ENABLE_GET_WORDCLOUD", False)


def test_independent_runs_get_isolated_directories_even_with_same_keywords(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    first = AsyncFileWriter("xhs", "search")
    first_path = Path(first._get_file_path("jsonl", "contents"))
    monkeypatch.setattr(config, "SAVE_DATA_RUN_ID", "")
    second = AsyncFileWriter("xhs", "search")
    second_path = Path(second._get_file_path("jsonl", "contents"))

    assert first.run_id != second.run_id
    assert first_path != second_path
    assert first_path.parent.parent.name == first.run_id
    assert second_path.parent.parent.name == second.run_id


def test_contents_and_comments_share_one_run_and_append_within_that_run(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    contents = AsyncFileWriter("xhs", "search")
    comments = AsyncFileWriter("xhs", "search")
    asyncio.run(contents.write_to_jsonl({"id": "first"}, "contents"))
    asyncio.run(contents.write_to_jsonl({"id": "second"}, "contents"))
    asyncio.run(comments.write_to_jsonl({"id": "comment"}, "comments"))
    contents_path = Path(contents._get_file_path("jsonl", "contents"))
    comments_path = Path(comments._get_file_path("jsonl", "comments"))

    assert contents.run_id == comments.run_id
    assert contents_path.parent.parent == comments_path.parent.parent
    assert [json.loads(line)["id"] for line in contents_path.read_text(encoding="utf-8").splitlines()] == ["first", "second"]
    metadata = json.loads((contents_path.parent.parent / "run_metadata.json").read_text(encoding="utf-8"))
    assert metadata["run_id"] == contents.run_id
    assert metadata["keywords"] == ["same", "keyword"]


def test_explicit_save_path_still_creates_an_isolated_run_subdirectory(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path / "custom-output")
    writer = AsyncFileWriter("xhs", "search")

    path = Path(writer._get_file_path("csv", "contents"))

    assert path.is_relative_to(tmp_path / "custom-output")
    assert writer.run_id in path.parts
