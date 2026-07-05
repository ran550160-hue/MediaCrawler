import json
from pathlib import Path

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.dataset_bundle_exporter import DatasetBundleExporter
from mediacrawler_mcp.dataset_importer import DatasetImporter
from mediacrawler_mcp.normalizer import DatasetNormalizer
from mediacrawler_mcp.storage import Storage


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _config(tmp_path: Path) -> McpConfig:
    return McpConfig(
        home=tmp_path / "mcp-home",
        browser_mode="persistent_context",
        cdp_endpoint=None,
        max_concurrent_tasks=1,
        default_timeout_seconds=300,
    )


def test_exporter_builds_bundle_from_latest_desktop_jsonl_and_normalizes(tmp_path):
    data_root = tmp_path / "data"
    output_dir = tmp_path / "inbox"
    jsonl_dir = data_root / "xhs" / "jsonl"
    _write_jsonl(jsonl_dir / "search_contents_2026-07-01.jsonl", [{"note_id": "old"}])
    _write_jsonl(
        jsonl_dir / "search_contents_2026-07-02.jsonl",
        [
            {
                "note_id": "n1",
                "title": "AI coding side job",
                "desc": "real user feedback",
                "liked_count": "10",
                "comment_count": "1",
                "source_keyword": "ai coding",
            }
        ],
    )
    _write_jsonl(
        jsonl_dir / "search_comments_2026-07-02.jsonl",
        [
            {
                "note_id": "n1",
                "comment_id": "c1",
                "content": "risk and pricing matter",
                "like_count": "3",
                "source_keyword": "ai coding",
            }
        ],
    )

    result = DatasetBundleExporter().export_xhs_bundle(
        name="Desktop Export",
        output_dir=output_dir,
        keywords=["ai coding"],
        description="desktop captured data",
        data_root=data_root,
        crawler_type="search",
        dataset_id="desktop_bundle",
    )

    bundle_dir = output_dir / "desktop_bundle"
    manifest = json.loads((bundle_dir / "dataset.json").read_text(encoding="utf-8"))
    contents_rows = [
        json.loads(line)
        for line in (bundle_dir / "raw" / "xhs_contents.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert result["dataset_id"] == "desktop_bundle"
    assert manifest["metrics"] == {"content_count": 1, "comment_count": 1}
    assert manifest["options"]["contents_source"].endswith("search_contents_2026-07-02.jsonl")
    assert contents_rows[0]["note_id"] == "n1"

    config = _config(tmp_path)
    storage = Storage(config)
    DatasetImporter(config, storage).register_dataset(str(bundle_dir), import_mode="copy")
    summary = DatasetNormalizer(storage).normalize_dataset("desktop_bundle", force=True)

    assert summary["content_count"] == 1
    assert summary["comment_count"] == 1


def test_exporter_converts_explicit_json_and_csv_inputs_to_jsonl(tmp_path):
    contents_path = tmp_path / "contents.json"
    comments_path = tmp_path / "comments.csv"
    contents_path.write_text(
        json.dumps([{"note_id": "n1", "title": "json content"}], ensure_ascii=False),
        encoding="utf-8",
    )
    comments_path.write_text(
        "note_id,comment_id,content,like_count\nn1,c1,csv comment,5\n",
        encoding="utf-8",
    )

    result = DatasetBundleExporter().export_xhs_bundle(
        name="Mixed Export",
        output_dir=tmp_path / "inbox",
        keywords=["mixed"],
        contents_path=contents_path,
        comments_path=comments_path,
        dataset_id="mixed_bundle",
    )

    bundle_dir = Path(result["dataset_dir"])
    comments_rows = [
        json.loads(line)
        for line in (bundle_dir / "raw" / "xhs_comments.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert result["metrics"] == {"content_count": 1, "comment_count": 1}
    assert comments_rows == [{"note_id": "n1", "comment_id": "c1", "content": "csv comment", "like_count": "5"}]
