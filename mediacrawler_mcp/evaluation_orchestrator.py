from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from mediacrawler_mcp.evaluation_workflow import EvaluationWorkflow
from mediacrawler_mcp.evidence_analysis import EvidenceAnalysis
from mediacrawler_mcp.evidence_ledger import EvidenceLedger
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.utils import utc_now_iso


CARD_VERSION = "evaluation-card-v1"


class EvaluationOrchestrator:
    """Small interface for card-validated evaluation preparation and lifecycle state."""

    def __init__(self, storage: Storage, runs_root: str | Path):
        self.storage, self.runs_root = storage, Path(runs_root)
        self.ledger, self.analysis = EvidenceLedger(storage), EvidenceAnalysis(storage)
        self.workflow = EvaluationWorkflow(self.analysis)

    def prepare(self, card: dict[str, Any], *, dataset_id: str | None = None, research_run_id: str | None = None) -> dict[str, Any]:
        card = self.validate_card(card)
        if bool(dataset_id) == bool(research_run_id):
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Provide exactly one of dataset_id or research_run_id")
        if dataset_id:
            row = self.storage.get_dataset_row(dataset_id)
            if row is None:
                raise McpAppError(ErrorCode.DATASET_NOT_FOUND, "Dataset not found", dataset_id)
            run = self.ledger.create_research_run(card["research_question"], card["platforms"], card["keywords"], sample_limits={"evaluation_card": card["name"]})
            self.ledger.build_evidence_items(run.research_run_id, dataset_id)
            research_run_id = run.research_run_id
        run = self.ledger.get_research_run(str(research_run_id))
        if run.status != "completed":
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Evaluation requires a completed frozen ResearchRun")
        result = self.analysis.analyze(research_run_id, card["analysis"]["near_duplicate_threshold"], card["analysis"]["duplicate_input_scope"])
        evaluation_id = f"evaluation_{uuid.uuid4().hex[:16]}"
        directory = self.runs_root / evaluation_id
        if directory.exists():
            raise McpAppError(ErrorCode.RESOURCE_BUSY, "Evaluation directory already exists", evaluation_id)
        directory.mkdir(parents=True, exist_ok=False)
        bundle = self.workflow.export_annotation_bundle(result["analysis_run_id"], directory, sample_size=card["sampling"]["sample_size"], seed=card["sampling"]["seed"])
        evaluation = {"evaluation_schema_version": "evaluation-v1", "evaluation_id": evaluation_id, "created_at": utc_now_iso(), "evaluation_status": "prepared", "research_run_id": research_run_id, "analysis_run_id": result["analysis_run_id"], "evidence_set_fingerprint": result["evidence_set_fingerprint"], "card_name": card["name"], "report_status": "not_generated"}
        self._write(directory / "evaluation_card.json", card)
        self._write(directory / "evaluation.json", evaluation)
        return {**evaluation, "directory": str(directory), "sampling": {"population_size": bundle["manifest"]["population_size"], "sample_size": bundle["manifest"]["sample_size"], "pair_count": bundle["manifest"]["duplicate_pair_sampling"]["pair_count"]}}

    def validate(self, directory: str | Path, *, formal: bool = False) -> dict[str, Any]:
        directory = Path(directory)
        card, evaluation = self._load(directory)
        relevance, pairs = self._completed_paths(directory)
        if formal and card["annotation"]["duplicate_gold_completeness"] != "complete":
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Formal evaluation requires complete duplicate gold declaration")
        if formal:
            rows = [json.loads(line) for line in relevance.read_text(encoding="utf-8").splitlines() if line.strip()]
            if any(not all(str(row.get(key) or "").strip() for key in ("annotator", "annotated_at", "annotation_reason")) for row in rows):
                raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Formal evaluation requires complete annotation provenance")
        report = self.workflow.import_and_evaluate(evaluation["analysis_run_id"], relevance, pairs, directory, provisional=not formal)
        gate = self._quality_gate(card, report) if formal else "quality_gate_not_evaluable"
        return {"valid": True, "evaluation_id": evaluation["evaluation_id"], "mode": "formal" if formal else "provisional", "annotation_completeness": report["annotation_completeness"], "quality_gate_status": gate}

    def evaluate(self, directory: str | Path, mode: str) -> dict[str, Any]:
        if mode not in {"provisional", "formal"}:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "mode must be provisional or formal")
        result = self.validate(directory, formal=mode == "formal")
        _, evaluation = self._load(Path(directory))
        evaluation["evaluation_status"] = "formal_evaluation_complete" if mode == "formal" else "provisional_evaluation_complete"
        evaluation["report_status"] = "generated"
        evaluation["quality_gate_status"] = result["quality_gate_status"]
        self._write(Path(directory) / "evaluation.json", evaluation)
        return {**result, "evaluation_status": evaluation["evaluation_status"]}

    def status(self, directory: str | Path) -> dict[str, Any]:
        card, evaluation = self._load(Path(directory))
        manifest = json.loads((Path(directory) / "sampling_manifest.json").read_text(encoding="utf-8"))
        rel, pairs = self._completed_paths(Path(directory), required=False)
        return {"evaluation_id": evaluation["evaluation_id"], "research_run_id": evaluation["research_run_id"], "analysis_run_id": evaluation["analysis_run_id"], "evidence_set_fingerprint": evaluation["evidence_set_fingerprint"], "evaluation_status": evaluation["evaluation_status"], "report_status": evaluation["report_status"], "quality_gate_status": evaluation.get("quality_gate_status", "quality_gate_not_evaluable"), "versions": {"card": card["schema_version"], "sampling": manifest["sampling_schema_version"]}, "sampling": {"population_size": manifest["population_size"], "sample_size": manifest["sample_size"], "pair_count": manifest["duplicate_pair_sampling"]["pair_count"]}, "annotation_files_present": {"relevance": rel.exists(), "duplicate_pairs": pairs.exists()}}

    @staticmethod
    def validate_card(card: dict[str, Any]) -> dict[str, Any]:
        required = {"schema_version", "name", "research_question", "platforms", "keywords", "sampling", "analysis", "annotation"}
        allowed = required | {"quality_gate"}
        if not isinstance(card, dict) or set(card) - allowed or required - set(card) or card.get("schema_version") != CARD_VERSION:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Invalid evaluation-card-v1 fields")
        sampling, analysis, annotation = card["sampling"], card["analysis"], card["annotation"]
        if set(sampling) != {"sample_size", "seed", "strategy"} or not isinstance(sampling["sample_size"], int) or sampling["sample_size"] < 1 or not isinstance(sampling["seed"], int) or sampling["strategy"] != "stratified_random_without_replacement":
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Invalid sampling configuration")
        if set(analysis) != {"duplicate_input_scope", "near_duplicate_threshold"} or analysis["duplicate_input_scope"] not in {"included_uncertain", "all"} or not isinstance(analysis["near_duplicate_threshold"], (int, float)) or not 0 < analysis["near_duplicate_threshold"] <= 1:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Invalid analysis configuration")
        if set(annotation) != {"mode", "duplicate_gold_completeness"} or annotation["mode"] != "independent_blind" or annotation["duplicate_gold_completeness"] not in {"complete", "incomplete"}:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Invalid annotation configuration")
        if not all(isinstance(card[key], str) and card[key].strip() for key in ("name", "research_question")) or not all(isinstance(card[key], list) and card[key] and all(isinstance(v, str) and v.strip() for v in card[key]) for key in ("platforms", "keywords")):
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Card name, question, platforms, and keywords are required")
        return card

    def _quality_gate(self, card: dict[str, Any], report: dict[str, Any]) -> str:
        gate = card.get("quality_gate")
        if not gate:
            return "quality_gate_not_evaluable"
        if not isinstance(gate, dict) or set(gate) - {"min_included_precision", "min_included_recall", "max_false_exclusion_rate"}:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Invalid quality_gate")
        metrics = report["relevance"]
        checks = [(metrics["included_precision"], gate.get("min_included_precision"), True), (metrics["included_recall"], gate.get("min_included_recall"), True), (metrics["false_exclusion_rate"], gate.get("max_false_exclusion_rate"), False)]
        if any(value is None for value, threshold, _ in checks if threshold is not None): return "quality_gate_not_evaluable"
        return "quality_gate_passed" if all(threshold is None or (value >= threshold if lower else value <= threshold) for value, threshold, lower in checks) else "quality_gate_not_met"

    def _load(self, directory: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        if not directory.is_dir(): raise McpAppError(ErrorCode.NOT_FOUND, "Evaluation directory not found", str(directory))
        card = json.loads((directory / "evaluation_card.json").read_text(encoding="utf-8")); self.validate_card(card)
        return card, json.loads((directory / "evaluation.json").read_text(encoding="utf-8"))

    @staticmethod
    def _completed_paths(directory: Path, required: bool = True) -> tuple[Path, Path]:
        paths = (directory / "relevance_annotations.completed.jsonl", directory / "duplicate_pair_annotations.completed.jsonl")
        if required and not all(path.exists() for path in paths): raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Completed annotation files are required")
        return paths

    @staticmethod
    def _write(path: Path, value: dict[str, Any]) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
