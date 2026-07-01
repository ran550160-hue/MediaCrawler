import json

import pandas as pd
import pytest
from typer.testing import CliRunner

from analysis.cli import app
from analysis.reports import (
    AnalysisReportError,
    generate_analysis_report,
    normalize_contents,
    normalize_comments,
)


runner = CliRunner()


def _fake_word_cloud(word_freq, output_path):
    output_path.write_bytes(b"fake-png")
    return bool(word_freq)


@pytest.fixture
def xhs_dataframes():
    contents = pd.DataFrame(
        [
            {
                "note_id": "note-1",
                "title": "First",
                "nickname": "Alice",
                "note_url": "https://example.com/note-1",
                "source_keyword": "python",
                "user_id": "u1",
                "liked_count": "10",
                "collected_count": "2",
                "comment_count": "3",
                "share_count": "1",
            },
            {
                "note_id": "note-2",
                "title": "Second",
                "nickname": "Bob",
                "note_url": "https://example.com/note-2",
                "source_keyword": "python",
                "user_id": "u2",
                "liked_count": "bad",
                "collected_count": "",
                "comment_count": "5",
                "share_count": None,
            },
        ]
    )
    comments = pd.DataFrame(
        [
            {
                "comment_id": "c1",
                "note_id": "note-1",
                "content": "python python data",
                "user_id": "cu1",
                "nickname": "Commenter 1",
                "like_count": "8",
            },
            {
                "comment_id": "c2",
                "note_id": "note-1",
                "content": "data analysis",
                "user_id": "cu2",
                "nickname": "Commenter 2",
                "like_count": "",
            },
        ]
    )
    return contents, comments


def test_platform_field_mapping_and_numeric_normalization(xhs_dataframes):
    contents, comments = xhs_dataframes

    normalized_contents = normalize_contents(contents, "xhs")
    normalized_comments = normalize_comments(comments, "xhs")

    assert normalized_contents.loc[0, "_content_id"] == "note-1"
    assert normalized_contents.loc[0, "_url"] == "https://example.com/note-1"
    assert normalized_contents.loc[0, "engagement_count"] == 16
    assert normalized_contents.loc[1, "engagement_count"] == 5
    assert normalized_comments.loc[0, "_content_id"] == "note-1"
    assert normalized_comments.loc[1, "like_count"] == 0


def test_douyin_field_mapping():
    contents = pd.DataFrame(
        [{"aweme_id": "aweme-1", "aweme_url": "https://example.com/a", "liked_count": "1"}]
    )
    comments = pd.DataFrame([{"comment_id": "c1", "aweme_id": "aweme-1", "content": "hello"}])

    assert normalize_contents(contents, "dy").loc[0, "_content_id"] == "aweme-1"
    assert normalize_contents(contents, "dy").loc[0, "_url"] == "https://example.com/a"
    assert normalize_comments(comments, "dy").loc[0, "_content_id"] == "aweme-1"


def test_generate_analysis_report_with_contents_and_comments(tmp_path, monkeypatch, xhs_dataframes):
    contents, comments = xhs_dataframes
    monkeypatch.setattr("analysis.reports.write_word_cloud", _fake_word_cloud)

    summary = generate_analysis_report("xhs", contents, comments, tmp_path, top_n=1)

    assert summary["metrics"]["total_contents"] == 2
    assert summary["metrics"]["total_comments"] == 2
    assert summary["metrics"]["unique_creators"] == 2
    assert summary["metrics"]["unique_comment_users"] == 2
    assert summary["keyword_distribution"] == {"python": 2}
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "report.html").exists()
    assert (tmp_path / "top_contents.csv").exists()
    assert (tmp_path / "top_comments.csv").exists()
    assert (tmp_path / "comment_word_freq.json").exists()
    assert (tmp_path / "comment_word_cloud.png").exists()

    top_contents = pd.read_csv(tmp_path / "top_contents.csv")
    assert top_contents.loc[0, "content_id"] == "note-1"


def test_generate_analysis_report_deduplicates_content_and_comments(tmp_path, monkeypatch):
    contents = pd.DataFrame(
        [
            {"note_id": "n1", "title": "low", "liked_count": "1", "comment_count": "0"},
            {"note_id": "n1", "title": "high", "liked_count": "10", "comment_count": "2"},
        ]
    )
    comments = pd.DataFrame(
        [
            {"comment_id": "c1", "note_id": "n1", "content": "old", "like_count": "1"},
            {"comment_id": "c1", "note_id": "n1", "content": "new", "like_count": "9"},
        ]
    )
    monkeypatch.setattr("analysis.reports.write_word_cloud", _fake_word_cloud)

    summary = generate_analysis_report("xhs", contents, comments, tmp_path)

    assert summary["metrics"]["raw_total_contents"] == 2
    assert summary["metrics"]["total_contents"] == 1
    assert summary["metrics"]["duplicate_contents_removed"] == 1
    assert summary["metrics"]["raw_total_comments"] == 2
    assert summary["metrics"]["total_comments"] == 1
    assert summary["metrics"]["duplicate_comments_removed"] == 1
    top_contents = pd.read_csv(tmp_path / "top_contents.csv")
    top_comments = pd.read_csv(tmp_path / "top_comments.csv")
    assert top_contents.loc[0, "title"] == "high"
    assert top_comments.loc[0, "content"] == "new"


def test_generate_analysis_report_allows_content_only(tmp_path, xhs_dataframes):
    contents, _ = xhs_dataframes

    summary = generate_analysis_report("xhs", contents, None, tmp_path, top_n=5)

    assert summary["metrics"]["total_contents"] == 2
    assert summary["metrics"]["total_comments"] == 0
    assert (tmp_path / "top_contents.csv").exists()


def test_generate_analysis_report_allows_comments_only(tmp_path, monkeypatch, xhs_dataframes):
    _, comments = xhs_dataframes
    monkeypatch.setattr("analysis.reports.write_word_cloud", _fake_word_cloud)

    summary = generate_analysis_report("xhs", None, comments, tmp_path, top_n=5)

    assert summary["metrics"]["total_contents"] == 0
    assert summary["metrics"]["total_comments"] == 2
    assert summary["top_commented_contents"][0] == {"content_id": "note-1", "comment_count": 2}


def test_generate_analysis_report_rejects_empty_inputs(tmp_path):
    with pytest.raises(AnalysisReportError):
        generate_analysis_report("xhs", None, None, tmp_path)


def test_cli_runs_with_explicit_files(tmp_path, monkeypatch, xhs_dataframes):
    contents, comments = xhs_dataframes
    contents_path = tmp_path / "contents.jsonl"
    comments_path = tmp_path / "comments.jsonl"
    contents_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in contents.to_dict("records")),
        encoding="utf-8",
    )
    comments_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in comments.to_dict("records")),
        encoding="utf-8",
    )
    output_dir = tmp_path / "analysis"
    monkeypatch.setattr("analysis.reports.write_word_cloud", _fake_word_cloud)

    result = runner.invoke(
        app,
        [
            "--platform",
            "xhs",
            "--contents",
            str(contents_path),
            "--comments",
            str(comments_path),
            "--output-dir",
            str(output_dir),
            "--top-n",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert "Analysis complete" in result.output
    assert (output_dir / "summary.json").exists()


def test_cli_fails_when_no_inputs_are_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--platform", "xhs"])

    assert result.exit_code == 1
    assert "no contents or comments file found" in result.output


def test_cli_dy_auto_discovers_douyin_directory(tmp_path, monkeypatch):
    data_dir = tmp_path / "data" / "douyin" / "jsonl"
    data_dir.mkdir(parents=True)
    contents_path = data_dir / "search_contents_2026-06-27.jsonl"
    comments_path = data_dir / "search_comments_2026-06-27.jsonl"
    contents_path.write_text(
        json.dumps(
            {
                "aweme_id": "a1",
                "aweme_url": "https://example.com/a1",
                "title": "Douyin",
                "liked_count": "1",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    comments_path.write_text(
        json.dumps(
            {
                "comment_id": "c1",
                "aweme_id": "a1",
                "content": "douyin analysis",
                "like_count": "2",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("analysis.reports.write_word_cloud", _fake_word_cloud)

    result = runner.invoke(app, ["--platform", "dy"])

    assert result.exit_code == 0
    assert (tmp_path / "data" / "douyin" / "analysis" / "summary.json").exists()
