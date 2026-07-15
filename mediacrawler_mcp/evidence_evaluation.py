from __future__ import annotations

from collections import defaultdict
from math import sqrt
from typing import Any

from mediacrawler_mcp.evidence_analysis import EvidenceAnalysis


class OfflineEvaluator:
    """Evaluate one immutable analysis run against versioned human annotations."""

    def __init__(self, analysis: EvidenceAnalysis):
        self.analysis = analysis

    def evaluate(
        self,
        analysis_run_id: str,
        annotations: list[dict[str, Any]],
        duplicate_pair_annotations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        run = self.analysis.get_analysis_run(analysis_run_id)
        decisions = {row["evidence_id"]: row for row in self.analysis.list_relevance_decisions(analysis_run_id)}
        gold = self._validated_relevance_annotations(annotations, decisions, run)
        pairs = self._validated_pair_annotations(duplicate_pair_annotations or [], decisions, run)
        sample_decisions = {key: decisions[key] for key in gold}

        labels = ("included", "excluded", "uncertain")
        matrix = {
            expected: {predicted: 0 for predicted in labels}
            for expected in labels
        }
        for evidence_id, annotation in gold.items():
            matrix[annotation["expected_relevance"]][decisions[evidence_id]["decision"]] += 1

        predicted_included = {key for key, row in decisions.items() if row["decision"] == "included" and key in gold}
        predicted_excluded = {key for key, row in decisions.items() if row["decision"] == "excluded" and key in gold}
        predicted_candidates = {key for key, row in decisions.items() if row["decision"] in {"included", "uncertain"} and key in gold}
        expected_included = {key for key, row in gold.items() if row["expected_relevance"] == "included"}
        expected_excluded = {key for key, row in gold.items() if row["expected_relevance"] == "excluded"}
        expected_candidates = {key for key, row in gold.items() if row["expected_relevance"] in {"included", "uncertain"}}
        false_exclusions = expected_candidates & predicted_excluded
        included_tp = len(predicted_included & expected_included)
        included_fp = len(predicted_included - expected_included)
        included_fn = len(expected_included - predicted_included)
        included_tn = len(gold) - included_tp - included_fp - included_fn
        relevance = {
            "included_precision": self._precision(predicted_included, expected_included),
            "included_recall": self._recall(predicted_included, expected_included),
            "excluded_precision": self._precision(predicted_excluded, expected_excluded),
            "uncertain_rate": self._ratio(sum(1 for row in sample_decisions.values() if row["decision"] == "uncertain"), len(sample_decisions)),
            "decision_coverage": self._ratio(sum(1 for row in sample_decisions.values() if row["decision"] in {"included", "excluded"}), len(sample_decisions)),
            "candidate_precision": self._precision(predicted_candidates, expected_candidates),
            "candidate_recall": self._recall(predicted_candidates, expected_candidates),
            "false_exclusion_rate": self._ratio(len(false_exclusions), len(expected_candidates)),
            "prediction_distribution_all_evidence": self._decision_counts(decisions.values()),
            "prediction_distribution_annotated_sample": self._decision_counts(sample_decisions.values()),
            "confusion_matrix": matrix,
            "false_positives": sorted(predicted_included - expected_included),
            "false_negatives": sorted(expected_included - predicted_included),
            "false_exclusions": sorted(false_exclusions),
            "binary_included_counts": {"tp": included_tp, "fp": included_fp, "fn": included_fn, "tn": included_tn},
            "confidence_intervals_95": {
                "included_precision": self._wilson_interval(included_tp, len(predicted_included)),
                "included_recall": self._wilson_interval(included_tp, len(expected_included)),
                "excluded_precision": self._wilson_interval(len(predicted_excluded & expected_excluded), len(predicted_excluded)),
                "candidate_precision": self._wilson_interval(len(predicted_candidates & expected_candidates), len(predicted_candidates)),
                "candidate_recall": self._wilson_interval(len(predicted_candidates & expected_candidates), len(expected_candidates)),
            },
        }
        edges = self.analysis.list_duplicate_candidate_edges(analysis_run_id)
        predicted_exact = {self._pair(row) for row in edges if row["edge_type"] == "exact"}
        predicted_near = {self._pair(row) for row in edges if row["edge_type"] == "near"}
        predicted_body = {self._pair(row) for row in edges if row["edge_type"] == "body_only_exact"}
        gold_pairs = self._expected_exact_pairs(gold.values())
        gold_available = any(row.get("expected_duplicate_group") for row in gold.values())
        pair_labels = {self._pair(row): row["expected_relation"] for row in pairs}
        candidate_pairs = predicted_exact | predicted_near | predicted_body
        annotated_pairs = {self._pair(row) for row in pairs}
        exact_metrics = self._detector_metrics(predicted_exact, pair_labels, gold_pairs, gold_available)
        body_metrics = self._detector_metrics(predicted_body, pair_labels, gold_pairs, gold_available)
        near_metrics = self._detector_metrics(predicted_near, pair_labels, gold_pairs, gold_available)
        candidate_evidence = {evidence_id for pair in candidate_pairs for evidence_id in pair}
        eligible_pairs = len(decisions) * (len(decisions) - 1) // 2
        return {
            "analysis_run_id": analysis_run_id,
            "research_run_id": run["research_run_id"],
            "evidence_set_fingerprint": run["evidence_set_fingerprint"],
            "annotation_count": len(gold),
            "relevance": relevance,
            "duplicates": {
                "exact": exact_metrics, "body_only_exact": body_metrics, "near": near_metrics,
                "exact_pair_precision": exact_metrics["precision"], "exact_pair_recall": exact_metrics["recall"],
                "near_duplicate_candidate_recall": self._recall(candidate_pairs, gold_pairs) if gold_available else None,
                "misclassified_candidate_pairs": [
                    row for row in edges
                    if row["edge_type"] in {"exact", "near", "body_only_exact"} and self._pair(row) in {self._pair(pair) for pair in pairs if pair["expected_relation"] == "not_duplicate"}
                ],
                "chain_expansion": self._chain_expansion(analysis_run_id, edges),
                "predicted_but_unannotated_pair_count": len(candidate_pairs - annotated_pairs),
                "precision_denominator_pairs": {"exact": exact_metrics["precision_denominator_pairs"], "body_only_exact": body_metrics["precision_denominator_pairs"], "near": near_metrics["precision_denominator_pairs"]},
                "exact_predicted_pair_count": len(predicted_exact), "body_only_candidate_pair_count": len(predicted_body), "near_candidate_pair_count": len(predicted_near),
                "exact_predicted_pairs_all_human_confirmed": None if not predicted_exact else all(pair_labels.get(pair) == "duplicate" for pair in predicted_exact),
                "candidate_pairs_per_evidence": self._ratio(len(candidate_pairs), len(decisions)),
                "candidate_pairs_per_involved_evidence": self._ratio(len(candidate_pairs), len(candidate_evidence)),
                "candidate_expansion_ratio": self._ratio(len(candidate_pairs), eligible_pairs),
            },
        }

    @staticmethod
    def _validated_relevance_annotations(annotations: list[dict[str, Any]], decisions: dict[str, Any], run: dict[str, Any]) -> dict[str, dict[str, Any]]:
        required = {"research_question", "research_run_id", "evidence_set_fingerprint", "evidence_id", "expected_relevance", "annotation_reason", "annotator", "annotated_at", "annotation_schema_version"}
        result: dict[str, dict[str, Any]] = {}
        for row in annotations:
            missing = required - row.keys()
            if missing:
                raise ValueError(f"Annotation missing required fields: {sorted(missing)}")
            if row["research_run_id"] != run["research_run_id"] or row["evidence_set_fingerprint"] != run["evidence_set_fingerprint"]:
                raise ValueError("Annotation research_run_id or evidence_set_fingerprint does not match analysis run")
            if row["evidence_id"] not in decisions:
                raise ValueError(f"Annotation references unknown evidence_id: {row['evidence_id']}")
            if row["expected_relevance"] not in {"included", "excluded", "uncertain"}:
                raise ValueError("expected_relevance must be included, excluded, or uncertain")
            previous = result.get(row["evidence_id"])
            if previous and any(previous.get(key) != row.get(key) for key in ("expected_relevance", "expected_duplicate_group")):
                raise ValueError(f"Conflicting annotations for evidence_id: {row['evidence_id']}")
            result[row["evidence_id"]] = row
        return result

    @staticmethod
    def _validated_pair_annotations(annotations: list[dict[str, Any]], decisions: dict[str, Any], run: dict[str, Any]) -> list[dict[str, Any]]:
        required = {"research_run_id", "evidence_set_fingerprint", "left_evidence_id", "right_evidence_id", "expected_relation", "annotation_reason"}
        seen: dict[tuple[str, str], str] = {}
        result = []
        for row in annotations:
            missing = required - row.keys()
            if missing:
                raise ValueError(f"Pair annotation missing required fields: {sorted(missing)}")
            pair = OfflineEvaluator._pair(row)
            if pair[0] == pair[1] or any(evidence_id not in decisions for evidence_id in pair):
                raise ValueError("Pair annotation must refer to two distinct evidence IDs in the analysis run")
            if row["research_run_id"] != run["research_run_id"] or row["evidence_set_fingerprint"] != run["evidence_set_fingerprint"]:
                raise ValueError("Pair annotation research_run_id or evidence_set_fingerprint does not match analysis run")
            if row["expected_relation"] not in {"duplicate", "not_duplicate", "uncertain"}:
                raise ValueError("expected_relation must be duplicate, not_duplicate, or uncertain")
            if pair in seen and seen[pair] != row["expected_relation"]:
                raise ValueError(f"Conflicting pair annotations: {pair}")
            seen[pair] = row["expected_relation"]
            result.append(row)
        return result

    @staticmethod
    def _pair(row: dict[str, Any]) -> tuple[str, str]:
        return tuple(sorted((row["left_evidence_id"], row["right_evidence_id"])))

    @staticmethod
    def _expected_exact_pairs(annotations: Any) -> set[tuple[str, str]]:
        groups = defaultdict(list)
        for row in annotations:
            if row.get("expected_duplicate_group"):
                groups[row["expected_duplicate_group"]].append(row["evidence_id"])
        return {(left, right) for ids in groups.values() for index, left in enumerate(sorted(ids)) for right in sorted(ids)[index + 1:]}

    @staticmethod
    def _precision(predicted: set[Any], expected: set[Any]) -> float:
        return len(predicted & expected) / len(predicted) if predicted else None

    @staticmethod
    def _recall(predicted: set[Any], expected: set[Any]) -> float:
        return len(predicted & expected) / len(expected) if expected else None

    @classmethod
    def _detector_metrics(cls, predicted: set[tuple[str, str]], labels: dict[tuple[str, str], str], gold_pairs: set[tuple[str, str]], gold_available: bool) -> dict[str, Any]:
        resolved = {pair for pair in predicted if labels.get(pair) in {"duplicate", "not_duplicate"}}
        uncertain = {pair for pair in predicted if labels.get(pair) == "uncertain"}
        unresolved = predicted - resolved - uncertain
        positives = {pair for pair in resolved if labels[pair] == "duplicate"}
        return {"pair_count": len(predicted), "precision": cls._precision(resolved, positives), "recall": cls._recall(predicted, gold_pairs) if gold_available else None, "resolved_annotation_coverage": cls._ratio(len(resolved), len(predicted)), "uncertain_pair_count": len(uncertain), "unresolved_predicted_pair_count": len(unresolved), "precision_denominator_pairs": [list(pair) for pair in sorted(resolved)]}

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 0.0

    @staticmethod
    def _wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> dict[str, float | None]:
        """95% Wilson interval; returns null bounds when the denominator is zero."""
        if total <= 0:
            return {"estimate": 0.0, "lower": None, "upper": None, "n": 0}
        estimate = successes / total
        denominator = 1 + z * z / total
        centre = (estimate + z * z / (2 * total)) / denominator
        margin = z * sqrt((estimate * (1 - estimate) + z * z / (4 * total)) / total) / denominator
        return {"estimate": estimate, "lower": max(0.0, centre - margin), "upper": min(1.0, centre + margin), "n": total}

    @staticmethod
    def _decision_counts(decisions: Any) -> dict[str, int]:
        counts = {"included": 0, "excluded": 0, "uncertain": 0}
        for row in decisions:
            counts[row["decision"]] = counts.get(row["decision"], 0) + 1
        return counts

    def _chain_expansion(self, analysis_run_id: str, edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups = self.analysis.list_duplicate_candidate_groups(analysis_run_id)
        pairs = {self._pair(edge) for edge in edges}
        return [{"duplicate_group_id": group["duplicate_group_id"], "missing_direct_pairs": [pair for index, left in enumerate(group["evidence_ids"]) for pair in [(left, right) for right in group["evidence_ids"][index + 1:]] if pair not in pairs]} for group in groups if len(group["evidence_ids"]) > 2]
