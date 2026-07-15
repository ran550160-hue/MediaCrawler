import json
import sqlite3
import shutil
import threading
from pathlib import Path

import duckdb
import pytest

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.dataset_service import DatasetService
from mediacrawler_mcp.evidence_ledger import EvidenceLedger, MAX_PAGE_LIMIT
from mediacrawler_mcp.normalizer import DatasetNormalizer
from mediacrawler_mcp.storage import Storage


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "evidence_ledger"


def _services(tmp_path):
    config = McpConfig(home=tmp_path, browser_mode="persistent_context", cdp_endpoint=None, max_concurrent_tasks=1, default_timeout_seconds=300)
    storage = Storage(config)
    return DatasetService(config, storage), DatasetNormalizer(storage), EvidenceLedger(storage), storage


def _fixture_record(platform="xhs"):
    path = FIXTURE_DIR / f"{platform}_contents.jsonl"
    return json.loads(path.read_text(encoding="utf-8"))


def _dataset(tmp_path, platform="xhs", records=None):
    dataset_service, normalizer, ledger, storage = _services(tmp_path)
    dataset = dataset_service.create_dataset(
        name=f"{platform} evidence fixture", platforms=[platform], keywords=["AI编程"],
        options={"collection_task_id": f"collect_{platform}_fixture", "collection_completed_at": "2026-07-02T12:00:00+00:00"},
    )
    records = records or [_fixture_record(platform)]
    raw_path = Path(dataset.dataset_dir) / "raw" / f"{platform}_contents.jsonl"
    raw_path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8")
    normalizer.normalize_dataset(dataset.dataset_id)
    return dataset, normalizer, ledger, storage


def _run(ledger, platforms=("xhs",)):
    return ledger.create_research_run(
        question="AI 编程接单有哪些可验证的风险？", platforms=list(platforms), keywords=["AI编程"],
        time_range={"start": "2026-07-01", "end": "2026-07-31"}, sample_limits={"contents_per_platform": 10},
        collection_task_ids=[f"collect_{platforms[0]}_fixture"],
    )


def _detail(ledger, run_id):
    page = ledger.list_evidence_items(run_id)
    return ledger.get_evidence_item(run_id, page["evidence_items"][0]["evidence_id"])


def test_same_input_rebuild_is_idempotent_and_completed_run_is_frozen(tmp_path):
    dataset, _, ledger, _ = _dataset(tmp_path)
    run = _run(ledger)
    first = ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)
    second = ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)

    assert first["already_completed"] is False
    assert second == {**second, "already_completed": True, "status": "completed"}
    assert _detail(ledger, run.research_run_id).evidence_id == first["evidence_ids"][0]


def test_body_change_creates_new_immutable_evidence_version_in_new_run(tmp_path):
    dataset, normalizer, ledger, _ = _dataset(tmp_path)
    old_run = _run(ledger)
    ledger.build_evidence_items(old_run.research_run_id, dataset.dataset_id)
    old = _detail(ledger, old_run.research_run_id)

    raw_path = Path(dataset.dataset_dir) / "raw" / "xhs_contents.jsonl"
    changed = json.loads(raw_path.read_text(encoding="utf-8"))
    changed["desc"] = "正文变更后必须形成新的不可变证据版本。"
    raw_path.write_text(json.dumps(changed, ensure_ascii=False) + "\n", encoding="utf-8")
    normalizer.normalize_dataset(dataset.dataset_id, force=True)
    new_run = _run(ledger)
    ledger.build_evidence_items(new_run.research_run_id, dataset.dataset_id)
    new = _detail(ledger, new_run.research_run_id)

    assert old.source_entity_id == new.source_entity_id
    assert old.content_hash != new.content_hash
    assert old.evidence_id != new.evidence_id
    assert old.full_text == "先明确报价、交付边界和验收标准。"
    assert new.full_text == "正文变更后必须形成新的不可变证据版本。"


def test_same_source_entity_has_multiple_evidence_versions_across_runs(tmp_path):
    dataset, normalizer, ledger, _ = _dataset(tmp_path)
    first_run = _run(ledger)
    ledger.build_evidence_items(first_run.research_run_id, dataset.dataset_id)
    first = _detail(ledger, first_run.research_run_id)
    raw_path = Path(dataset.dataset_dir) / "raw" / "xhs_contents.jsonl"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["title"] = "AI 编程接单经验（修订版）"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False) + "\n", encoding="utf-8")
    normalizer.normalize_dataset(dataset.dataset_id, force=True)
    second_run = _run(ledger)
    ledger.build_evidence_items(second_run.research_run_id, dataset.dataset_id)
    second = _detail(ledger, second_run.research_run_id)
    versions = ledger.list_evidence_versions("xhs", "normal", "xhs-evidence-001")

    assert first.source_entity_id == second.source_entity_id
    assert {first.evidence_id, second.evidence_id}.__len__() == 2
    assert {item["evidence_id"] for item in versions["evidence_items"]} == {first.evidence_id, second.evidence_id}


def test_multiple_keyword_discoveries_are_preserved_in_association_table(tmp_path):
    first = _fixture_record()
    first.update({"source_keyword": "AI编程", "collection_task_id": "task_a", "result_position": 1})
    second = dict(first)
    second.update({"source_keyword": "自由职业", "collection_task_id": "task_b", "result_position": 2})
    dataset, _, ledger, _ = _dataset(tmp_path, records=[first, second])
    run = _run(ledger)
    ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)

    evidence = _detail(ledger, run.research_run_id)
    paths = {(item["keyword"], item["collection_task_id"], item["result_position"]) for item in evidence.discovery_contexts}
    assert paths == {("AI编程", "task_a", 1), ("自由职业", "task_b", 2)}


def test_engagement_only_change_creates_observation_not_content_version(tmp_path):
    first = _fixture_record()
    first.update({"liked_count": "120", "collection_task_id": "task_a"})
    second = dict(first)
    second.update({"liked_count": "999", "collection_task_id": "task_b"})
    dataset, _, ledger, _ = _dataset(tmp_path, records=[first, second])
    run = _run(ledger)
    result = ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)
    evidence = _detail(ledger, run.research_run_id)

    assert result["evidence_count"] == 1
    assert len(evidence.observations) == 2
    assert len({item["raw_record_hash"] for item in evidence.observations}) == 2
    assert evidence.content_hash == EvidenceLedger._hash({"title": EvidenceLedger._canonical_text(evidence.title), "full_text": EvidenceLedger._canonical_text(evidence.full_text)})


def test_portable_raw_reference_resolves_after_datasets_root_moves(tmp_path):
    dataset, _, ledger, _ = _dataset(tmp_path, "douyin")
    run = _run(ledger, ("douyin",))
    ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)
    reference = _detail(ledger, run.research_run_id).raw_record_reference
    moved_root = tmp_path / "moved-project" / "datasets"
    shutil.copytree(dataset.dataset_dir, moved_root / dataset.dataset_id)

    resolved = ledger.resolve_raw_record_path(reference, moved_root)
    assert reference["dataset_relative_raw_path"] == "raw/douyin_contents.jsonl"
    assert "D:" not in json.dumps(reference)
    assert resolved.exists()


def test_failed_build_is_not_completed_and_does_not_persist_half_finished_ledger(tmp_path, monkeypatch):
    dataset, _, ledger, storage = _dataset(tmp_path)
    run = _run(ledger)

    def fail(*args, **kwargs):
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(storage, "complete_evidence_build", fail)
    with pytest.raises(RuntimeError, match="injected"):
        ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)

    failed = ledger.get_research_run(run.research_run_id)
    assert failed.status == "build_failed"
    assert failed.build_completed_at is None
    assert ledger.list_evidence_items(run.research_run_id)["total"] == 0


def test_list_evidence_items_pages_filters_and_caps_limit_without_raw_metadata(tmp_path):
    records = []
    for number in range(3):
        record = _fixture_record()
        record["note_id"] = f"xhs-page-{number}"
        record["title"] = f"页面证据 {number}"
        record["desc"] = " ".join(f"token-{index}" for index in range(1_200))
        records.append(record)
    dataset, _, ledger, _ = _dataset(tmp_path, records=records)
    run = _run(ledger)
    ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)

    first_page = ledger.list_evidence_items(run.research_run_id, platform="xhs", limit=1, offset=0)
    capped_page = ledger.list_evidence_items(run.research_run_id, content_type="normal", limit=999)
    item = first_page["evidence_items"][0]
    assert first_page["total"] == 3 and first_page["next_offset"] == 1
    assert capped_page["limit"] == MAX_PAGE_LIMIT and capped_page["total"] == 3
    assert "metadata" not in item and "raw" not in item
    assert item["full_text_truncated"] is True and len(item["full_text"]) == 1_000


def test_same_content_id_on_different_platform_or_content_type_does_not_collide(tmp_path):
    raw = _fixture_record()
    raw["note_id"] = "shared-id"
    dataset, _, ledger, _ = _dataset(tmp_path, records=[raw])
    db_path = Path(dataset.dataset_dir) / "analysis.duckdb"
    with duckdb.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO contents SELECT * REPLACE ('douyin' AS platform, 'video' AS content_type) FROM contents")
    run = _run(ledger, ("xhs", "douyin"))
    ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)

    page = ledger.list_evidence_items(run.research_run_id, limit=10)
    assert page["total"] == 2
    assert len({item["evidence_id"] for item in page["evidence_items"]}) == 2
    assert len({item["source_entity_id"] for item in page["evidence_items"]}) == 2


def test_research_run_records_versions_and_xhs_douyin_evidence_fields(tmp_path):
    dataset, _, ledger, _ = _dataset(tmp_path)
    run = _run(ledger)
    ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)
    loaded = ledger.get_research_run(run.research_run_id)
    evidence = _detail(ledger, run.research_run_id)

    assert loaded.status == "completed"
    assert loaded.source_dataset_id == dataset.dataset_id
    assert loaded.dataset_version.startswith(f"{dataset.dataset_id}:")
    assert loaded.normalization_version
    assert loaded.ledger_schema_version == "evidence-ledger/v2"
    assert evidence.platform == "xhs"
    assert evidence.raw_record_reference["platform_record_id_field"] == "note_id"


def test_content_hash_is_not_an_aggressive_near_duplicate_normalizer(tmp_path):
    _, _, ledger, _ = _dataset(tmp_path)
    first = EvidenceLedger._hash({"title": "A!", "full_text": "hello, world"})
    second = EvidenceLedger._hash({"title": "a", "full_text": "hello world"})
    assert first != second
    assert EvidenceLedger._canonical_text("  A\r\nB  ") == "A\nB"


def test_source_entity_identity_covers_content_and_comment_lineage(tmp_path):
    _, _, ledger, _ = _dataset(tmp_path)
    note = ledger._source_entity_id("xhs", "normal", "same", {})
    video = ledger._source_entity_id("douyin", "video", "same", {})
    root_comment = ledger._source_entity_id("xhs", "comment", "same", {}, parent_content_id="note-1")
    reply = ledger._source_entity_id("xhs", "comment", "same", {}, parent_content_id="note-1", parent_comment_id="comment-1")
    assert len({note, video, root_comment, reply}) == 4


def test_source_snapshot_change_during_build_fails_without_completion(tmp_path, monkeypatch):
    dataset, _, ledger, _ = _dataset(tmp_path)
    run = _run(ledger)
    original = ledger._dataset_version
    calls = 0

    def changing_version(*args):
        nonlocal calls
        calls += 1
        return "snapshot-before" if calls == 1 else "snapshot-after"

    monkeypatch.setattr(ledger, "_dataset_version", changing_version)
    with pytest.raises(Exception, match="Source snapshot changed"):
        ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)
    assert ledger.get_research_run(run.research_run_id).status == "build_failed"
    monkeypatch.setattr(ledger, "_dataset_version", original)


def test_build_lease_compare_and_set_allows_only_one_concurrent_owner(tmp_path):
    _, _, ledger, storage = _dataset(tmp_path)
    run = _run(ledger)
    barrier = threading.Barrier(2)
    results = []

    def claim():
        barrier.wait()
        results.append(storage.mark_evidence_build_started(
            run.research_run_id, source_dataset_id="ds", dataset_version="v", normalization_version="n",
            ledger_schema_version="l", hash_algorithm_version="h", started_at="now",
        ))

    first, second = threading.Thread(target=claim), threading.Thread(target=claim)
    first.start(); second.start(); first.join(); second.join()
    assert sorted(results) == [False, True]


def test_failed_and_stale_builds_are_explicitly_recoverable_and_retryable(tmp_path, monkeypatch):
    dataset, _, ledger, storage = _dataset(tmp_path)
    run = _run(ledger)
    original = storage.complete_evidence_build
    monkeypatch.setattr(storage, "complete_evidence_build", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)
    monkeypatch.setattr(storage, "complete_evidence_build", original)
    assert ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)["status"] == "completed"

    stale = _run(ledger)
    stale_started_at = "2020-01-01T00:00:00+00:00"
    assert storage.mark_evidence_build_started(stale.research_run_id, source_dataset_id="ds", dataset_version="v", normalization_version="n", ledger_schema_version="l", hash_algorithm_version="h", started_at=stale_started_at)
    assert ledger.recover_stale_build(stale.research_run_id, stale_started_at, min_age_seconds=0)["recovered"] is True
    assert ledger.get_research_run(stale.research_run_id).status == "build_failed"


def test_pr1_sqlite_schema_upgrades_without_reusing_legacy_unique_constraint(tmp_path):
    config = McpConfig(home=tmp_path, browser_mode="persistent_context", cdp_endpoint=None, max_concurrent_tasks=1, default_timeout_seconds=300)
    config.home.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(config.metadata_db_path) as conn:
        conn.execute("CREATE TABLE research_runs (research_run_id TEXT PRIMARY KEY, question TEXT NOT NULL, platforms_json TEXT NOT NULL, keywords_json TEXT NOT NULL, time_range_json TEXT, sample_limits_json TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL, collection_task_ids_json TEXT NOT NULL, dataset_version TEXT NOT NULL)")
        conn.execute("CREATE TABLE evidence_items (evidence_id TEXT PRIMARY KEY, research_run_id TEXT NOT NULL, platform TEXT NOT NULL, content_type TEXT NOT NULL, content_id TEXT NOT NULL, author_id TEXT NOT NULL, source_url TEXT NOT NULL, published_at TEXT, collected_at TEXT, query_keyword TEXT NOT NULL, title TEXT NOT NULL, full_text TEXT NOT NULL, raw_content_hash TEXT NOT NULL, canonical_content_hash TEXT NOT NULL, engagement_json TEXT NOT NULL, raw_record_reference_json TEXT NOT NULL, metadata_json TEXT NOT NULL)")
    storage = Storage(config)
    storage.initialize()
    with sqlite3.connect(config.metadata_db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"evidence_items", "evidence_item_versions", "evidence_discovery_contexts"} <= tables


def test_raw_reference_rejects_absolute_and_escaping_paths(tmp_path):
    _, _, ledger, _ = _dataset(tmp_path)
    base = {"dataset_id": "ds", "dataset_relative_raw_path": "raw/a.jsonl"}
    with pytest.raises(Exception, match="must stay"):
        ledger.resolve_raw_record_path({**base, "dataset_relative_raw_path": "D:/escape.jsonl"})
    with pytest.raises(Exception, match="must stay"):
        ledger.resolve_raw_record_path({**base, "dataset_relative_raw_path": "raw/../../escape.jsonl"})


def test_completed_run_rejects_a_different_source_snapshot(tmp_path):
    dataset, normalizer, ledger, _ = _dataset(tmp_path)
    run = _run(ledger)
    ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)
    raw_path = Path(dataset.dataset_dir) / "raw" / "xhs_contents.jsonl"
    changed = json.loads(raw_path.read_text(encoding="utf-8")); changed["desc"] = "changed"
    raw_path.write_text(json.dumps(changed, ensure_ascii=False) + "\n", encoding="utf-8")
    normalizer.normalize_dataset(dataset.dataset_id, force=True)
    with pytest.raises(Exception, match="frozen"):
        ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)


def test_hash_algorithm_and_revision_identity_are_persisted(tmp_path):
    dataset, _, ledger, _ = _dataset(tmp_path)
    run = _run(ledger)
    ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)
    evidence = _detail(ledger, run.research_run_id)
    assert run.hash_algorithm_version == "sha256-canonical-json-nfc-v1"
    assert evidence.hash_algorithm_version == run.hash_algorithm_version
    assert evidence.content_revision_id.startswith("revision_")
