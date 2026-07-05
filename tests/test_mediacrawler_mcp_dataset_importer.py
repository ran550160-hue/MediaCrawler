import json
from pathlib import Path

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.dataset_importer import DatasetImporter
from mediacrawler_mcp.dataset_service import DatasetService
from mediacrawler_mcp.normalizer import DatasetNormalizer
from mediacrawler_mcp.storage import Storage


def _config(tmp_path: Path) -> McpConfig:
    return McpConfig(
        home=tmp_path,
        browser_mode="persistent_context",
        cdp_endpoint=None,
        max_concurrent_tasks=1,
        default_timeout_seconds=300,
    )


def _importer(tmp_path: Path) -> tuple[DatasetImporter, Storage]:
    config = _config(tmp_path)
    storage = Storage(config)
    return DatasetImporter(config, storage), storage


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _make_bundle(tmp_path: Path, dataset_id: str = "desktop_ds") -> Path:
    bundle = tmp_path / "inbox" / dataset_id
    (bundle / "raw").mkdir(parents=True)
    (bundle / "media").mkdir()
    (bundle / "logs").mkdir()
    (bundle / "dataset.json").write_text(
        json.dumps(
            {
                "dataset_id": dataset_id,
                "name": "Desktop Export",
                "description": "exported from desktop browser",
                "platforms": ["xhs"],
                "keywords": ["ai coding"],
                "options": {"collector": "desktop"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_jsonl(
        bundle / "raw" / "xhs_contents.jsonl",
        [
            {
                "note_id": "n1",
                "title": "first",
                "desc": "content",
                "source_keyword": "ai coding",
            }
        ],
    )
    _write_jsonl(
        bundle / "raw" / "xhs_comments.jsonl",
        [
            {
                "note_id": "n1",
                "comment_id": "c1",
                "content": "useful",
                "source_keyword": "ai coding",
            },
            {
                "note_id": "n1",
                "comment_id": "c2",
                "content": "risky",
                "source_keyword": "ai coding",
            },
        ],
    )
    return bundle


def test_validate_dataset_bundle_accepts_contents_only_with_warning(tmp_path):
    importer, _ = _importer(tmp_path)
    bundle = tmp_path / "desktop-export"
    (bundle / "raw").mkdir(parents=True)
    _write_jsonl(bundle / "raw" / "xhs_contents.jsonl", [{"note_id": "n1", "title": "only contents"}])

    result = importer.validate_dataset_bundle(str(bundle))

    assert result["valid"] is True
    assert result["metadata"]["platforms"] == ["xhs"]
    assert result["raw_files"]["contents"]["line_count"] == 1
    assert result["raw_files"]["comments"]["exists"] is False
    assert any("xhs_comments" in warning for warning in result["warnings"])


def test_register_dataset_copies_bundle_and_writes_sqlite_metadata(tmp_path):
    importer, storage = _importer(tmp_path)
    bundle = _make_bundle(tmp_path, dataset_id="desktop_ds")

    result = importer.register_dataset(str(bundle), import_mode="copy")

    dataset_dir = tmp_path / "datasets" / "desktop_ds"
    manifest = json.loads((dataset_dir / "dataset.json").read_text(encoding="utf-8"))
    row = storage.get_dataset_row("desktop_ds")

    assert result["dataset_id"] == "desktop_ds"
    assert result["dataset_dir"] == str(dataset_dir.resolve())
    assert dataset_dir.exists()
    assert (dataset_dir / "raw" / "xhs_contents.jsonl").exists()
    assert manifest["metrics"] == {"content_count": 1, "comment_count": 2}
    assert manifest["files"]["raw"]["contents"]["line_count"] == 1
    assert row is not None
    assert row["content_count"] == 1
    assert row["comment_count"] == 2


def test_registered_bundle_can_be_normalized(tmp_path):
    importer, storage = _importer(tmp_path)
    bundle = _make_bundle(tmp_path, dataset_id="normalizable_ds")
    importer.register_dataset(str(bundle), import_mode="copy")

    summary = DatasetNormalizer(storage).normalize_dataset("normalizable_ds", force=True)

    assert summary["content_count"] == 1
    assert summary["comment_count"] == 2
    assert Path(summary["duckdb_path"]).exists()


def test_import_raw_files_copies_into_existing_dataset_and_syncs_manifest(tmp_path):
    config = _config(tmp_path)
    storage = Storage(config)
    dataset = DatasetService(config, storage).create_dataset(
        name="Existing Dataset",
        platforms=["xhs"],
        keywords=["ai coding"],
    )
    importer = DatasetImporter(config, storage)
    raw_source = tmp_path / "desktop-raw"
    contents_path = raw_source / "contents.jsonl"
    comments_path = raw_source / "comments.jsonl"
    _write_jsonl(contents_path, [{"note_id": "n1", "title": "raw"}])
    _write_jsonl(comments_path, [{"note_id": "n1", "comment_id": "c1", "content": "comment"}])

    result = importer.import_raw_files(
        dataset_id=dataset.dataset_id,
        platform="xhs",
        contents_path=str(contents_path),
        comments_path=str(comments_path),
        source_keyword="ai coding",
    )

    dataset_dir = Path(dataset.dataset_dir)
    manifest = json.loads((dataset_dir / "dataset.json").read_text(encoding="utf-8"))
    row = storage.get_dataset_row(dataset.dataset_id)

    assert result["raw_files"]["contents"] == str(dataset_dir / "raw" / "xhs_contents.jsonl")
    assert result["manifest"]["metrics"] == {"content_count": 1, "comment_count": 1}
    assert manifest["files"]["raw"]["comments"]["line_count"] == 1
    assert manifest["options"]["last_import_source_keyword"] == "ai coding"
    assert row is not None
    assert row["content_count"] == 1
    assert row["comment_count"] == 1
