import json
from pathlib import Path

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.dataset_service import DatasetService
from mediacrawler_mcp.evidence_analysis import EvidenceAnalysis
from mediacrawler_mcp.evidence_evaluation import OfflineEvaluator
from mediacrawler_mcp.evaluation_workflow import EvaluationWorkflow
from mediacrawler_mcp.claim_validation import ClaimValidator
from mediacrawler_mcp.evidence_ledger import EvidenceLedger
from mediacrawler_mcp.models import ClaimDraft
from mediacrawler_mcp.normalizer import DatasetNormalizer
from mediacrawler_mcp.storage import Storage


def _prepared_run(tmp_path, records=None):
    config = McpConfig(home=tmp_path, browser_mode="persistent_context", cdp_endpoint=None, max_concurrent_tasks=1, default_timeout_seconds=300)
    storage = Storage(config)
    service, ledger = DatasetService(config, storage), EvidenceLedger(storage)
    dataset = service.create_dataset(name="analysis", platforms=["xhs"], keywords=["AI编程"])
    records = records or [
        {"note_id": "a", "title": "AI编程 接单指南", "desc": "AI编程接单要先确认交付边界并且书面记录验收标准", "user_id": "u1", "source_keyword": "AI编程"},
        {"note_id": "b", "title": "AI编程 接单指南", "desc": "AI编程接单要先确认交付边界并且书面记录验收标准", "user_id": "u2", "source_keyword": "AI编程"},
        {"note_id": "c", "title": "AI编程 接单技巧", "desc": "AI编程接单先确认交付范围并且书面说明验收标准", "user_id": "u3", "source_keyword": "AI编程"},
        {"note_id": "d", "title": "烘焙入门", "desc": "今天学习烤面包", "user_id": "u4", "source_keyword": "烘焙"},
    ]
    raw = Path(dataset.dataset_dir) / "raw" / "xhs_contents.jsonl"
    raw.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
    DatasetNormalizer(storage).normalize_dataset(dataset.dataset_id)
    run = ledger.create_research_run("AI编程接单的证据", ["xhs"], ["AI编程"])
    ledger.build_evidence_items(run.research_run_id, dataset.dataset_id)
    return storage, ledger, run


def test_analysis_writes_explainable_relevance_and_duplicate_candidates(tmp_path):
    storage, ledger, run = _prepared_run(tmp_path)
    before = ledger.list_evidence_items(run.research_run_id, limit=10)["evidence_items"]

    result = EvidenceAnalysis(storage).analyze(run.research_run_id, near_duplicate_threshold=0.1)
    analysis = EvidenceAnalysis(storage)
    decisions = analysis.list_relevance_decisions(result["analysis_run_id"])
    groups = analysis.list_duplicate_candidate_groups(result["analysis_run_id"])
    after = ledger.list_evidence_items(run.research_run_id, limit=10)["evidence_items"]

    assert result["relevance_decision_count"] == 4
    assert before == after
    evidence_ids = {item["evidence_id"] for item in before}
    assert {item["evidence_id"] for item in decisions} == evidence_ids
    assert any(item["decision"] == "excluded" and item["reasons"][0]["reason"] for item in decisions)
    assert any(group["group_type"] == "exact" and len(group["evidence_ids"]) == 2 for group in groups)
    assert any(group["group_type"] == "near" and group["rationale"]["threshold"] == 0.1 for group in groups)
    assert all(set(group["evidence_ids"]) <= evidence_ids for group in groups)
    assert result["relevance_algorithm_version"] and result["duplicate_algorithm_version"]
    exact_members = next(group["evidence_ids"] for group in groups if group["group_type"] == "exact")
    annotations = [
        {"research_question": run.question, "research_run_id": run.research_run_id, "evidence_set_fingerprint": result["evidence_set_fingerprint"], "evidence_id": item["evidence_id"], "expected_relevance": item["decision"], "expected_duplicate_group": "copied" if item["evidence_id"] in exact_members else None, "annotation_reason": "fixture", "annotator": "test", "annotated_at": "2026-07-13T00:00:00+00:00", "annotation_schema_version": "evaluation-annotation-v2"}
        for item in decisions
    ]
    pairs = [{"research_run_id": run.research_run_id, "evidence_set_fingerprint": result["evidence_set_fingerprint"], "left_evidence_id": exact_members[0], "right_evidence_id": exact_members[1], "expected_relation": "duplicate", "annotation_reason": "same title and body"}]
    metrics = OfflineEvaluator(analysis).evaluate(result["analysis_run_id"], annotations, pairs)
    assert metrics["relevance"]["included_precision"] == 1.0
    assert metrics["relevance"]["confusion_matrix"]["included"]["included"] >= 1
    assert "exact_pair_precision" in metrics["duplicates"]
    pair_only_annotations = [{**row, "expected_duplicate_group": None} for row in annotations]
    pair_only_metrics = OfflineEvaluator(analysis).evaluate(result["analysis_run_id"], pair_only_annotations, pairs)
    assert pair_only_metrics["duplicates"]["exact"]["precision"] == 1.0
    assert pair_only_metrics["duplicates"]["exact"]["recall"] is None
    uncertain_pairs = [{**pairs[0], "expected_relation": "uncertain"}]
    uncertain_metrics = OfflineEvaluator(analysis).evaluate(result["analysis_run_id"], pair_only_annotations, uncertain_pairs)
    assert uncertain_metrics["duplicates"]["exact"]["precision"] is None


def test_evaluator_rejects_unbound_or_conflicting_annotations(tmp_path):
    storage, _, run = _prepared_run(tmp_path)
    result = EvidenceAnalysis(storage).analyze(run.research_run_id)
    analysis = EvidenceAnalysis(storage)
    evidence_id = analysis.list_relevance_decisions(result["analysis_run_id"])[0]["evidence_id"]
    annotation = {"research_question": run.question, "research_run_id": run.research_run_id, "evidence_set_fingerprint": result["evidence_set_fingerprint"], "evidence_id": evidence_id, "expected_relevance": "included", "expected_duplicate_group": None, "annotation_reason": "fixture", "annotator": "test", "annotated_at": "2026-07-13T00:00:00+00:00", "annotation_schema_version": "evaluation-annotation-v2"}
    evaluator = OfflineEvaluator(analysis)
    import pytest
    with pytest.raises(ValueError, match="unknown evidence_id"):
        evaluator.evaluate(result["analysis_run_id"], [{**annotation, "evidence_id": "missing"}])
    with pytest.raises(ValueError, match="Conflicting annotations"):
        evaluator.evaluate(result["analysis_run_id"], [annotation, {**annotation, "expected_relevance": "excluded"}])
    with pytest.raises(ValueError, match="fingerprint"):
        evaluator.evaluate(result["analysis_run_id"], [{**annotation, "evidence_set_fingerprint": "wrong"}])


def test_duplicate_fingerprint_is_structured_type_aware_and_short_text_is_not_high_confidence(tmp_path):
    storage, ledger, run = _prepared_run(tmp_path)
    result = EvidenceAnalysis(storage).analyze(run.research_run_id, near_duplicate_threshold=0.1)
    analysis = EvidenceAnalysis(storage)
    groups = analysis.list_duplicate_candidate_groups(result["analysis_run_id"])
    evidence = ledger.list_evidence_items(run.research_run_id, limit=10)["evidence_items"]
    first = ledger.get_evidence_item(run.research_run_id, evidence[0]["evidence_id"])
    from dataclasses import replace
    item = first
    assert analysis._exact_duplicate_fingerprint(item) != analysis._exact_duplicate_fingerprint(replace(item, content_type="comment"))
    assert analysis._exact_duplicate_fingerprint(item) != analysis._exact_duplicate_fingerprint(replace(item, title="changed title"))
    assert all(group["group_type"] != "exact" or all(len(ledger.get_evidence_item(run.research_run_id, evidence_id).full_text.strip()) >= 20 for evidence_id in group["evidence_ids"]) for group in groups)


def test_evaluator_reports_uncertain_and_candidate_filtering_metrics(tmp_path):
    storage, _, run = _prepared_run(tmp_path)
    result = EvidenceAnalysis(storage).analyze(run.research_run_id)
    analysis = EvidenceAnalysis(storage)
    decisions = analysis.list_relevance_decisions(result["analysis_run_id"])
    labels = ["uncertain", "included", "excluded", "included"]
    annotations = [
        {"research_question": run.question, "research_run_id": run.research_run_id, "evidence_set_fingerprint": result["evidence_set_fingerprint"], "evidence_id": decision["evidence_id"], "expected_relevance": label, "expected_duplicate_group": None, "annotation_reason": "metric fixture", "annotator": "test", "annotated_at": "2026-07-13T00:00:00+00:00", "annotation_schema_version": "evaluation-annotation-v2"}
        for decision, label in zip(decisions, labels, strict=True)
    ]
    metrics = OfflineEvaluator(analysis).evaluate(result["analysis_run_id"], annotations)
    relevance = metrics["relevance"]
    assert set(relevance) >= {"included_precision", "included_recall", "excluded_precision", "uncertain_rate", "decision_coverage", "candidate_precision", "candidate_recall", "false_exclusion_rate", "confusion_matrix"}
    assert sum(sum(row.values()) for row in relevance["confusion_matrix"].values()) == len(annotations)


def test_evaluation_workflow_exports_blind_templates_and_writes_reports(tmp_path):
    storage, _, run = _prepared_run(tmp_path)
    analysis = EvidenceAnalysis(storage)
    result = analysis.analyze(run.research_run_id)
    workflow = EvaluationWorkflow(analysis)
    output = tmp_path / "pilot"
    exported = workflow.export_annotation_bundle(result["analysis_run_id"], output, seed=7)
    assert exported["manifest"]["sampling_method"] == "census"
    template = [json.loads(line) for line in (output / "relevance_annotations.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all("decision" not in row and row["expected_relevance"] is None for row in template)
    for row in template:
        row.update(expected_relevance="included", annotation_reason="test", annotator="test", annotated_at="2026-07-13T00:00:00+00:00")
    relevance_path = output / "completed_relevance.jsonl"
    relevance_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in template), encoding="utf-8")
    pair_template = [json.loads(line) for line in (output / "duplicate_pair_annotations.jsonl").read_text(encoding="utf-8").splitlines()]
    for row in pair_template:
        row.update(expected_relation="not_duplicate", annotation_reason="test")
    pair_path = output / "completed_pairs.jsonl"
    pair_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in pair_template), encoding="utf-8")
    report = workflow.import_and_evaluate(result["analysis_run_id"], relevance_path, pair_path, output)
    assert report["evaluation_status"] == "provisional_pilot_not_final_acceptance"
    assert (output / "evaluation_report.json").exists() and (output / "evaluation_report.md").exists()
    incomplete_path = output / "incomplete_relevance.jsonl"
    incomplete_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in template[:-1]), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="exactly matching"):
        workflow.import_and_evaluate(result["analysis_run_id"], incomplete_path, pair_path, output, provisional=False)


def test_claim_validator_accepts_only_explicit_same_run_evidence(tmp_path):
    storage, ledger, run = _prepared_run(tmp_path)
    evidence_id = ledger.list_evidence_items(run.research_run_id, limit=1)["evidence_items"][0]["evidence_id"]
    claim = ClaimDraft("claim_manual_1", run.research_run_id, "Human-authored draft", [evidence_id], "observation")
    assert ClaimValidator(ledger).validate(claim)["valid"] is True
    import pytest
    with pytest.raises(Exception, match="outside its ResearchRun"):
        ClaimValidator(ledger).validate(ClaimDraft("claim_manual_2", run.research_run_id, "Draft", ["missing"], "observation"))


def test_body_only_candidates_export_and_evaluate_end_to_end(tmp_path):
    records = [
        {"note_id": "body-a", "title": "title one", "desc": "same long copied body for body only exact evaluation", "user_id": "a", "source_keyword": "AI缂栫▼"},
        {"note_id": "body-b", "title": "title two", "desc": "same long copied body for body only exact evaluation", "user_id": "b", "source_keyword": "AI缂栫▼"},
    ]
    storage, _, run = _prepared_run(tmp_path, records)
    analysis = EvidenceAnalysis(storage)
    result = analysis.analyze(run.research_run_id)
    assert any(edge["edge_type"] == "body_only_exact" for edge in analysis.list_duplicate_candidate_edges(result["analysis_run_id"]))
    output = tmp_path / "body"
    workflow = EvaluationWorkflow(analysis)
    workflow.export_annotation_bundle(result["analysis_run_id"], output)
    pairs = [json.loads(line) for line in (output / "duplicate_pair_annotations.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(pairs) == 1 and "left_evidence" in pairs[0]
    pairs[0].update(expected_relation="duplicate", annotation_reason="copy")
    (output / "pairs.completed.jsonl").write_text(json.dumps(pairs[0]) + "\n", encoding="utf-8")
    relevance = [json.loads(line) for line in (output / "relevance_annotations.jsonl").read_text(encoding="utf-8").splitlines()]
    for row in relevance:
        row.update(expected_relevance="included", annotation_reason="test", annotator="test", annotated_at="2026-07-13T00:00:00+00:00")
    (output / "relevance.completed.jsonl").write_text("".join(json.dumps(row) + "\n" for row in relevance), encoding="utf-8")
    report = workflow.import_and_evaluate(result["analysis_run_id"], output / "relevance.completed.jsonl", output / "pairs.completed.jsonl", output)
    assert report["duplicates"]["body_only_candidate_pair_count"] == 1


def test_analysis_reads_more_than_500_evidence_items(tmp_path):
    records = [{"note_id": f"n{index}", "title": "AI缂栫▼", "desc": f"unique content {index}", "user_id": f"u{index}", "source_keyword": "AI缂栫▼"} for index in range(501)]
    storage, _, run = _prepared_run(tmp_path, records)
    result = EvidenceAnalysis(storage).analyze(run.research_run_id)
    assert result["relevance_decision_count"] == 501
    assert result["configuration"]["research_run_evidence_count"] == result["configuration"]["analyzed_evidence_count"] == 501
