from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from mediacrawler_mcp.evidence_analysis import EvidenceAnalysis
from mediacrawler_mcp.evidence_evaluation import OfflineEvaluator
from mediacrawler_mcp.evidence_ledger import EvidenceLedger


class EvaluationWorkflow:
    """One deep seam for blind annotation export, validation, and reproducible reports."""

    def __init__(self, analysis: EvidenceAnalysis):
        self.analysis = analysis
        self.ledger = EvidenceLedger(analysis.storage)
        self.evaluator = OfflineEvaluator(analysis)

    def export_annotation_bundle(
        self, analysis_run_id: str, output_dir: str | Path, *, sample_size: int | None = None, seed: int = 20260713
    ) -> dict[str, Any]:
        run = self.analysis.get_analysis_run(analysis_run_id)
        decisions = self.analysis.list_relevance_decisions(analysis_run_id)
        evidence_by_id = {
            row["evidence_id"]: self.ledger.get_evidence_item(run["research_run_id"], row["evidence_id"])
            for row in decisions
        }
        decisions = [
            {**row, "platform": evidence_by_id[row["evidence_id"]].platform, "content_type": evidence_by_id[row["evidence_id"]].content_type}
            for row in decisions
        ]
        selected = self._stratified_sample(decisions, sample_size, seed)
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        evidence = {row["evidence_id"]: evidence_by_id[row["evidence_id"]] for row in selected}
        relevance_rows = [
            {
                "research_question": self.ledger.get_research_run(run["research_run_id"]).question,
                "research_run_id": run["research_run_id"],
                "evidence_set_fingerprint": run["evidence_set_fingerprint"],
                "evidence_id": item.evidence_id,
                "platform": item.platform,
                "content_type": item.content_type,
                "title": item.title,
                "full_text": item.full_text,
                "source_url": item.source_url,
                "expected_relevance": None,
                "expected_duplicate_group": None,
                "annotation_reason": "",
                "annotator": "",
                "annotated_at": "",
                "annotation_schema_version": "evaluation-annotation-v2",
            }
            for item in evidence.values()
        ]
        selected_ids = {row["evidence_id"] for row in selected}
        candidate_pairs = self._blind_pair_sample(selected, self.analysis.list_duplicate_candidate_edges(analysis_run_id), seed)
        pair_rows = [
            {
                "research_run_id": run["research_run_id"],
                "evidence_set_fingerprint": run["evidence_set_fingerprint"],
                "left_evidence_id": left,
                "right_evidence_id": right,
                "left_evidence": {"title": evidence_by_id[left].title, "full_text": evidence_by_id[left].full_text, "platform": evidence_by_id[left].platform, "content_type": evidence_by_id[left].content_type},
                "right_evidence": {"title": evidence_by_id[right].title, "full_text": evidence_by_id[right].full_text, "platform": evidence_by_id[right].platform, "content_type": evidence_by_id[right].content_type},
                "expected_relation": None,
                "annotation_reason": "",
            }
            for left, right in candidate_pairs
        ]
        manifest = {
            "sampling_schema_version": "evaluation-sampling-v2",
            "analysis_run_id": analysis_run_id,
            "research_run_id": run["research_run_id"],
            "evidence_set_fingerprint": run["evidence_set_fingerprint"],
            "population_size": len(decisions),
            "sample_size": len(selected),
            "sampling_method": "census" if len(selected) == len(decisions) else "stratified_random_without_replacement",
            "sampling_seed": seed,
            "selected_evidence_ids": [row["evidence_id"] for row in selected],
            "strata": self._strata(decisions, {row["evidence_id"] for row in selected}),
            "duplicate_pair_sampling": {
                "method": "all_predicted_candidates_plus_seeded_random_controls_blind_to_annotator",
                "sampling_seed": seed,
                "pair_count": len(pair_rows),
                "population": "all unordered pairs within the exported relevance sample",
                "selected_pairs": [list(pair) for pair in candidate_pairs],
            },
        }
        self._write_json(output / "sampling_manifest.json", manifest)
        self._write_jsonl(output / "relevance_annotations.jsonl", relevance_rows)
        self._write_jsonl(output / "duplicate_pair_annotations.jsonl", pair_rows)
        return {"manifest": manifest, "output_dir": str(output)}

    def import_and_evaluate(
        self, analysis_run_id: str, relevance_path: str | Path, duplicate_pair_path: str | Path, output_dir: str | Path, *, provisional: bool = True
    ) -> dict[str, Any]:
        relevance = self._read_jsonl(relevance_path)
        pairs = self._read_jsonl(duplicate_pair_path)
        manifest = json.loads((Path(relevance_path).parent / "sampling_manifest.json").read_text(encoding="utf-8"))
        run = self.analysis.get_analysis_run(analysis_run_id)
        if manifest.get("sampling_schema_version") != "evaluation-sampling-v2":
            raise ValueError("Sampling manifest version is not eligible for current formal evaluation")
        if any(manifest.get(key) != run.get(key) for key in ("analysis_run_id", "research_run_id", "evidence_set_fingerprint")):
            raise ValueError("Sampling manifest does not match the analysis run")
        expected_ids = set(manifest["selected_evidence_ids"])
        completed_ids = {row.get("evidence_id") for row in relevance}
        expected_pairs = {tuple(pair) for pair in manifest["duplicate_pair_sampling"]["selected_pairs"]}
        completed_pairs = {tuple(sorted((row.get("left_evidence_id"), row.get("right_evidence_id")))) for row in pairs}
        completeness = {"expected_relevance_count": len(expected_ids), "completed_relevance_count": len(completed_ids), "missing_evidence_ids": sorted(expected_ids - completed_ids), "extra_evidence_ids": sorted(completed_ids - expected_ids), "expected_pair_count": len(expected_pairs), "completed_pair_count": len(completed_pairs), "missing_pairs": [list(pair) for pair in sorted(expected_pairs - completed_pairs)], "extra_pairs": [list(pair) for pair in sorted(completed_pairs - expected_pairs)], "annotation_coverage": len(expected_ids & completed_ids) / len(expected_ids) if expected_ids else 1.0}
        duplicate_ids = len(completed_ids) != len(relevance)
        duplicate_pairs = len(completed_pairs) != len(pairs)
        schema_ok = all(row.get("annotation_schema_version") == "evaluation-annotation-v2" for row in relevance)
        completeness.update({"duplicate_evidence_ids": duplicate_ids, "duplicate_pairs": duplicate_pairs, "annotation_schema_version_valid": schema_ok})
        if not provisional and (completeness["missing_evidence_ids"] or completeness["extra_evidence_ids"] or completeness["missing_pairs"] or completeness["extra_pairs"] or duplicate_ids or duplicate_pairs or not schema_ok):
            raise ValueError("Formal acceptance requires annotations exactly matching the sampling manifest")
        report = self.evaluator.evaluate(analysis_run_id, relevance, pairs)
        report["evaluation_status"] = "provisional_pilot_not_final_acceptance" if provisional else "formal_acceptance"
        report["evaluation_report_schema_version"] = "evaluation-report-v2"
        report["analysis_configuration"] = run["configuration"]
        report["annotation_completeness"] = completeness
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        self._write_json(output / "evaluation_report.json", report)
        (output / "evaluation_report.md").write_text(self._markdown_report(report), encoding="utf-8")
        return report

    @staticmethod
    def _stratified_sample(decisions: list[dict[str, Any]], sample_size: int | None, seed: int) -> list[dict[str, Any]]:
        if sample_size is None or sample_size >= len(decisions):
            return sorted(decisions, key=lambda row: row["evidence_id"])
        rng = random.Random(seed)
        by_stratum: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in decisions:
            by_stratum[(row.get("platform", "unknown"), row.get("content_type", "unknown"), row["decision"])].append(row)
        selected: list[dict[str, Any]] = []
        for rows in by_stratum.values():
            if len(selected) >= sample_size:
                break
            selected.append(rng.choice(sorted(rows, key=lambda row: row["evidence_id"])))
        remaining = [row for row in decisions if row not in selected]
        rng.shuffle(remaining)
        selected.extend(remaining[: max(0, sample_size - len(selected))])
        return sorted(selected, key=lambda row: row["evidence_id"])

    @staticmethod
    def _strata(decisions: list[dict[str, Any]], selected: set[str]) -> list[dict[str, Any]]:
        values: dict[tuple[str, str, str], dict[str, int | str]] = {}
        for row in decisions:
            key = (row.get("platform", "unknown"), row.get("content_type", "unknown"), row["decision"])
            bucket = values.setdefault(key, {"platform": key[0], "content_type": key[1], "predicted_state": key[2], "population_size": 0, "sample_size": 0})
            bucket["population_size"] = int(bucket["population_size"]) + 1
            if row["evidence_id"] in selected:
                bucket["sample_size"] = int(bucket["sample_size"]) + 1
        return sorted(values.values(), key=lambda row: (str(row["platform"]), str(row["content_type"]), str(row["predicted_state"])))

    @staticmethod
    def _blind_pair_sample(decisions: list[dict[str, Any]], edges: list[dict[str, Any]], seed: int) -> list[tuple[str, str]]:
        ids = sorted(row["evidence_id"] for row in decisions)
        selected_ids = set(ids)
        predicted = {tuple(sorted((row["left_evidence_id"], row["right_evidence_id"]))) for row in edges if row["edge_type"] in {"exact", "near", "body_only_exact"} and row["left_evidence_id"] in selected_ids and row["right_evidence_id"] in selected_ids}
        universe = [(left, right) for index, left in enumerate(ids) for right in ids[index + 1:]]
        controls = [pair for pair in universe if pair not in predicted]
        rng = random.Random(seed)
        rng.shuffle(controls)
        return sorted(predicted | set(controls[: min(len(controls), max(10, len(predicted)))]))

    @staticmethod
    def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
        return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
        path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")

    @staticmethod
    def _markdown_report(report: dict[str, Any]) -> str:
        relevance, duplicates = report["relevance"], report["duplicates"]
        counts = relevance["binary_included_counts"]
        title = "Provisional pilot evaluation" if report["evaluation_status"].startswith("provisional") else "Formal evidence analysis evaluation"
        return "\n".join([
            f"# {title}", "", f"Status: **{report['evaluation_status']}**.", f"Analysis Run ID: `{report['analysis_run_id']}`", f"Configuration: `{report.get('analysis_configuration', {})}`", f"Annotation completeness: `{report.get('annotation_completeness', {})}`",
            "This report is not a final acceptance gate.", "",
            "## Relevance", "", f"- Included precision / recall: {EvaluationWorkflow._display(relevance['included_precision'])} / {EvaluationWorkflow._display(relevance['included_recall'])}",
            f"- Excluded precision: {EvaluationWorkflow._display(relevance['excluded_precision'])}; uncertain rate: {EvaluationWorkflow._display(relevance['uncertain_rate'])}; coverage: {EvaluationWorkflow._display(relevance['decision_coverage'])}",
            f"- Included TP/FP/FN/TN: {counts['tp']}/{counts['fp']}/{counts['fn']}/{counts['tn']}",
            f"- 95% Wilson CI (included precision): {relevance['confidence_intervals_95']['included_precision']}", "",
            f"- False positives: {relevance['false_positives']}", f"- False negatives: {relevance['false_negatives']}", "",
            "## Duplicate candidates", "", f"- Exact predicted pairs: {duplicates['exact_predicted_pair_count']}; all human-confirmed: {duplicates['exact_predicted_pairs_all_human_confirmed'] if duplicates['exact_predicted_pairs_all_human_confirmed'] is not None else 'not applicable (none predicted)'}",
            f"- Near candidate pairs: {duplicates['near_candidate_pair_count']}; predicted but unannotated: {duplicates['predicted_but_unannotated_pair_count']}",
            f"- Candidate pairs per evidence: {duplicates['candidate_pairs_per_evidence']:.3f}; expansion ratio: {duplicates['candidate_expansion_ratio']:.3f}",
            f"- Precision denominator pairs: {duplicates['precision_denominator_pairs']}",
            f"- Misclassified candidate edges: {duplicates['misclassified_candidate_pairs']}",
        ]) + "\n"

    @staticmethod
    def _display(value: Any) -> str:
        return "not_applicable" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)
