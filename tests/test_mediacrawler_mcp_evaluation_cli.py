import json
from pathlib import Path

import pytest

from mediacrawler_mcp.evaluation_orchestrator import EvaluationOrchestrator
from tests.test_mediacrawler_mcp_evidence_analysis import _prepared_run


def _card():
    return {"schema_version": "evaluation-card-v1", "name": "fixture", "research_question": "fixture question", "platforms": ["xhs"], "keywords": ["AI编程"], "sampling": {"sample_size": 4, "seed": 7, "strategy": "stratified_random_without_replacement"}, "analysis": {"duplicate_input_scope": "included_uncertain", "near_duplicate_threshold": 0.85}, "annotation": {"mode": "independent_blind", "duplicate_gold_completeness": "incomplete"}}


def _complete(directory: Path):
    rows = [json.loads(line) for line in (directory / "relevance_annotations.jsonl").read_text(encoding="utf-8").splitlines()]
    for row in rows: row.update(expected_relevance="included", annotation_reason="fixture", annotator="human", annotated_at="2026-07-14T00:00:00+00:00")
    (directory / "relevance_annotations.completed.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    pairs = [json.loads(line) for line in (directory / "duplicate_pair_annotations.jsonl").read_text(encoding="utf-8").splitlines()]
    for row in pairs: row.update(expected_relation="not_duplicate", annotation_reason="fixture")
    (directory / "duplicate_pair_annotations.completed.jsonl").write_text("".join(json.dumps(row) + "\n" for row in pairs), encoding="utf-8")


def test_generic_prepare_validate_evaluate_status(tmp_path):
    storage, _, run = _prepared_run(tmp_path)
    orchestrator = EvaluationOrchestrator(storage, tmp_path / "evaluation" / "runs")
    prepared = orchestrator.prepare(_card(), research_run_id=run.research_run_id)
    directory = Path(prepared["directory"])
    assert (directory / "sampling_manifest.json").exists()
    assert "decision" not in (directory / "relevance_annotations.jsonl").read_text(encoding="utf-8")
    _complete(directory)
    assert orchestrator.validate(directory)["valid"] is True
    assert orchestrator.evaluate(directory, "provisional")["evaluation_status"] == "provisional_evaluation_complete"
    assert orchestrator.status(directory)["report_status"] == "generated"


def test_prepare_rejects_bad_sources_and_directory_is_not_overwritten(tmp_path):
    storage, _, run = _prepared_run(tmp_path)
    orchestrator = EvaluationOrchestrator(storage, tmp_path / "runs")
    with pytest.raises(Exception): orchestrator.prepare(_card(), dataset_id="x", research_run_id=run.research_run_id)
    with pytest.raises(Exception): orchestrator.validate_card({**_card(), "unknown": True})
    prepared = orchestrator.prepare(_card(), research_run_id=run.research_run_id)
    assert Path(prepared["directory"]).exists()
    dataset_id = storage.get_research_run_row(run.research_run_id)["source_dataset_id"]
    dataset_prepared = orchestrator.prepare({**_card(), "name": "dataset-source"}, dataset_id=dataset_id)
    assert dataset_prepared["research_run_id"] != run.research_run_id
