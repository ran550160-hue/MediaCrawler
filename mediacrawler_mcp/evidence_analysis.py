from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from collections import defaultdict
from typing import Any

from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.evidence_ledger import EvidenceLedger
from mediacrawler_mcp.models import DuplicateCandidateGroup, EvidenceItem, RelevanceDecision
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.utils import utc_now_iso


RELEVANCE_ALGORITHM_VERSION = "keyword-substring-relevance-v1"
DUPLICATE_ALGORITHM_VERSION = "structured-exact-fingerprint-plus-type-aware-char-bigram-jaccard-v4"
DEFAULT_NEAR_DUPLICATE_THRESHOLD = 0.85
MIN_NEAR_DUPLICATE_CHARS = 20
MIN_HIGH_CONFIDENCE_EXACT_CHARS = 20


class EvidenceAnalysis:
    """Rule-based relevance and duplicate candidate analysis over immutable evidence."""

    def __init__(self, storage: Storage):
        self.storage = storage
        self.ledger = EvidenceLedger(storage)

    def analyze(self, research_run_id: str, near_duplicate_threshold: float = DEFAULT_NEAR_DUPLICATE_THRESHOLD, duplicate_input_scope: str = "included_uncertain") -> dict[str, Any]:
        run = self.ledger.get_research_run(research_run_id)
        if run.status != "completed":
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Evidence analysis requires a completed ResearchRun")
        threshold = float(near_duplicate_threshold)
        if not 0 < threshold <= 1:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "near_duplicate_threshold must be within (0, 1]")
        if duplicate_input_scope not in {"included_uncertain", "all"}:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "duplicate_input_scope must be included_uncertain or all")
        rows, expected_count, offset = [], None, 0
        while True:
            page, total = self.storage.list_evidence_version_rows(research_run_id, platform=None, content_type=None, limit=200, offset=offset)
            rows.extend(page)
            expected_count = total
            if offset + len(page) >= total:
                break
            offset += len(page)
        if expected_count != len(rows):
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Evidence analysis input is incomplete")
        evidence = [self.ledger._evidence_from_row(row) for row in rows]
        analysis_run_id = f"analysis_{uuid.uuid4().hex[:16]}"
        decisions = [self._relevance_decision(analysis_run_id, item, run.keywords) for item in evidence]
        near_ids = {item.evidence_id for item in decisions if duplicate_input_scope == "all" or item.decision in {"included", "uncertain"}}
        near_evidence = [item for item in evidence if item.evidence_id in near_ids]
        groups, edges, duplicate_counts = self._duplicate_groups(analysis_run_id, evidence, near_evidence, threshold)
        fingerprint = self._evidence_set_fingerprint(evidence)
        configuration = {"near_duplicate_threshold": threshold, "min_near_duplicate_chars": MIN_NEAR_DUPLICATE_CHARS, "duplicate_input_scope": duplicate_input_scope, "research_run_evidence_count": expected_count, "analyzed_evidence_count": len(evidence), **duplicate_counts}
        self.storage.create_evidence_analysis(
            analysis_run_id, research_run_id, RELEVANCE_ALGORITHM_VERSION,
            DUPLICATE_ALGORITHM_VERSION, configuration, utc_now_iso(), decisions, groups, edges, fingerprint,
        )
        return {
            "analysis_run_id": analysis_run_id,
            "research_run_id": research_run_id,
            "relevance_algorithm_version": RELEVANCE_ALGORITHM_VERSION,
            "duplicate_algorithm_version": DUPLICATE_ALGORITHM_VERSION,
            "configuration": configuration,
            "evidence_set_fingerprint": fingerprint,
            "relevance_decision_count": len(decisions),
            "duplicate_candidate_group_count": len(groups),
        }

    def list_relevance_decisions(self, analysis_run_id: str) -> list[dict[str, Any]]:
        return [
            {**row, "reasons": json.loads(row.pop("reasons_json"))}
            for row in self.storage.list_relevance_decision_rows(analysis_run_id)
        ]

    def list_duplicate_candidate_groups(self, analysis_run_id: str) -> list[dict[str, Any]]:
        return [
            {**row, "rationale": json.loads(row.pop("rationale_json"))}
            for row in self.storage.list_duplicate_group_rows(analysis_run_id)
        ]

    def list_duplicate_candidate_edges(self, analysis_run_id: str) -> list[dict[str, Any]]:
        return self.storage.list_duplicate_edge_rows(analysis_run_id)

    def get_analysis_run(self, analysis_run_id: str) -> dict[str, Any]:
        row = self.storage.get_evidence_analysis_row(analysis_run_id)
        if row is None:
            raise McpAppError(ErrorCode.NOT_FOUND, f"Analysis run not found: {analysis_run_id}")
        row["configuration"] = json.loads(row.pop("configuration_json"))
        return row

    @staticmethod
    def _relevance_decision(analysis_run_id: str, evidence: EvidenceItem, keywords: list[str]) -> RelevanceDecision:
        text = f"{evidence.title}\n{evidence.full_text}".casefold()
        matches = [keyword for keyword in keywords if keyword and keyword.casefold() in text]
        score = len(matches) / max(1, len(keywords))
        if matches:
            decision, reasons = "included", [{"rule": "keyword_substring", "matched_keywords": matches}]
        elif evidence.query_keyword in keywords:
            decision, reasons = "uncertain", [{"rule": "discovery_keyword_only", "reason": "discovery keyword matches but body has no verbatim match"}]
        else:
            decision, reasons = "excluded", [{"rule": "keyword_substring", "matched_keywords": [], "reason": "no configured keyword appears verbatim"}]
        return RelevanceDecision(analysis_run_id, evidence.evidence_id, decision, score, reasons, RELEVANCE_ALGORITHM_VERSION)

    def _duplicate_groups(self, analysis_run_id: str, evidence: list[EvidenceItem], near_evidence: list[EvidenceItem], threshold: float) -> tuple[list[DuplicateCandidateGroup], list[dict[str, Any]], dict[str, int]]:
        groups: list[DuplicateCandidateGroup] = []
        edges: list[dict[str, Any]] = []
        exact = defaultdict(list)
        body_only = defaultdict(list)
        eligible_exact = [item for item in evidence if len(self._normalized_body(item.full_text)) >= MIN_HIGH_CONFIDENCE_EXACT_CHARS]
        for item in eligible_exact:
            # A short identical reply such as "同意" is a useful low-confidence
            # candidate, but must not become a high-confidence exact duplicate.
            if len(self._normalized_body(item.full_text)) >= MIN_HIGH_CONFIDENCE_EXACT_CHARS:
                exact[self._exact_duplicate_fingerprint(item)].append(item)
                body_only[self._body_only_exact_fingerprint(item)].append(item)
        for fingerprint, members in exact.items():
            if len(members) > 1:
                ids = [item.evidence_id for item in members]
                groups.append(self._group(analysis_run_id, "exact", ids, {"rule": "same_exact_duplicate_fingerprint", "exact_duplicate_fingerprint": fingerprint}))
                for index, left in enumerate(sorted(ids)):
                    for right in sorted(ids)[index + 1:]:
                        edges.append(self._edge(left, right, "exact", 1.0, None))

        for fingerprint, members in body_only.items():
            # Do not duplicate an already exact (title + body) candidate group.
            title_fingerprints = {self._exact_duplicate_fingerprint(item) for item in members}
            if len(members) > 1 and len(title_fingerprints) > 1:
                ids = [item.evidence_id for item in members]
                groups.append(self._group(
                    analysis_run_id,
                    "body_only_exact",
                    ids,
                    {"rule": "same_body_only_exact_fingerprint", "body_only_exact_fingerprint": fingerprint,
                     "confidence": "candidate", "reason": "same body with different title"},
                ))
                for index, left in enumerate(sorted(ids)):
                    for right in sorted(ids)[index + 1:]:
                        edges.append(self._edge(left, right, "body_only_exact", 1.0, None))

        parent = {item.evidence_id: item.evidence_id for item in near_evidence}
        similar_pairs: list[dict[str, Any]] = []
        near_eligible = [item for item in near_evidence if len(item.full_text) >= MIN_NEAR_DUPLICATE_CHARS]
        cross_type_skipped, comparison_count = 0, 0
        for index, left in enumerate(near_eligible):
            for right in near_eligible[index + 1:]:
                if left.content_type != right.content_type:
                    cross_type_skipped += 1
                    continue
                comparison_count += 1
                score = self._jaccard(self._char_bigrams(left.full_text), self._char_bigrams(right.full_text))
                if score >= threshold:
                    self._union(parent, left.evidence_id, right.evidence_id)
                    pair = {"left_evidence_id": left.evidence_id, "right_evidence_id": right.evidence_id, "similarity": round(score, 6)}
                    similar_pairs.append(pair)
                    edges.append(self._edge(left.evidence_id, right.evidence_id, "near", score, threshold))
        components = defaultdict(list)
        for evidence_id in parent:
            components[self._find(parent, evidence_id)].append(evidence_id)
        for members in components.values():
            if len(members) > 1:
                pairs = [pair for pair in similar_pairs if pair["left_evidence_id"] in members and pair["right_evidence_id"] in members]
                groups.append(self._group(analysis_run_id, "near", sorted(members), {"rule": "char_bigram_jaccard", "threshold": threshold, "pairs": pairs}))
        return groups, edges, {"exact_scope_count": len(evidence), "exact_eligible_count": len(eligible_exact), "exact_skipped_short_count": len(evidence) - len(eligible_exact), "body_only_scope_count": len(evidence), "body_only_eligible_count": len(eligible_exact), "near_scope_count": len(near_evidence), "near_eligible_evidence_count": len(near_eligible), "near_skipped_short_count": len(near_evidence) - len(near_eligible), "near_pair_comparison_count": comparison_count, "near_skipped_cross_content_type_pair_count": cross_type_skipped}

    @staticmethod
    def _exact_duplicate_fingerprint(item: EvidenceItem) -> str:
        return EvidenceAnalysis._fingerprint({
            "fingerprint_schema_version": "exact-duplicate-v1",
            "content_type": item.content_type,
            "title": EvidenceAnalysis._normalized_body(item.title),
            "full_text": EvidenceAnalysis._normalized_body(item.full_text),
        })

    @staticmethod
    def _body_only_exact_fingerprint(item: EvidenceItem) -> str:
        return EvidenceAnalysis._fingerprint({
            "fingerprint_schema_version": "body-only-exact-v1",
            "content_type": item.content_type,
            "full_text": EvidenceAnalysis._normalized_body(item.full_text),
        })

    @staticmethod
    def _fingerprint(payload: dict[str, str]) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalized_body(text: str) -> str:
        return unicodedata.normalize("NFC", text).strip()

    @staticmethod
    def _edge(left: str, right: str, edge_type: str, similarity: float, threshold: float | None) -> dict[str, Any]:
        return {"left_evidence_id": min(left, right), "right_evidence_id": max(left, right), "edge_type": edge_type, "similarity": round(similarity, 6), "algorithm_version": DUPLICATE_ALGORITHM_VERSION, "threshold": threshold}

    @staticmethod
    def _evidence_set_fingerprint(evidence: list[EvidenceItem]) -> str:
        payload = [(item.evidence_id, item.content_revision_id, item.raw_record_hash) for item in sorted(evidence, key=lambda item: item.evidence_id)]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

    def _group(self, analysis_run_id: str, group_type: str, evidence_ids: list[str], rationale: dict[str, Any]) -> DuplicateCandidateGroup:
        identity = json.dumps({"analysis_run_id": analysis_run_id, "group_type": group_type, "evidence_ids": sorted(evidence_ids)}, sort_keys=True, separators=(",", ":"))
        group_id = f"duplicate_{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
        return DuplicateCandidateGroup(group_id, analysis_run_id, group_type, DUPLICATE_ALGORITHM_VERSION, rationale, sorted(evidence_ids))

    @staticmethod
    def _char_bigrams(text: str) -> set[str]:
        normalized = unicodedata.normalize("NFC", text).casefold()
        normalized = re.sub(r"\s+", "", normalized)
        return {normalized[index:index + 2] for index in range(max(0, len(normalized) - 1))} or ({normalized} if normalized else set())

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        union = left | right
        return len(left & right) / len(union) if union else 0.0

    @staticmethod
    def _find(parent: dict[str, str], value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    @classmethod
    def _union(cls, parent: dict[str, str], left: str, right: str) -> None:
        left_root, right_root = cls._find(parent, left), cls._find(parent, right)
        if left_root != right_root:
            parent[right_root] = left_root
