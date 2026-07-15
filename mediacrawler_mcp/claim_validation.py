from __future__ import annotations

from typing import Any

from mediacrawler_mcp.evidence_ledger import EvidenceLedger
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.models import ClaimDraft


class ClaimValidator:
    """Validate a human-authored ClaimDraft against one frozen evidence ledger.

    This is deliberately read-only: it neither creates a claim nor proposes text.
    """

    def __init__(self, ledger: EvidenceLedger):
        self.ledger = ledger

    def validate(self, claim: ClaimDraft) -> dict[str, Any]:
        run = self.ledger.get_research_run(claim.research_run_id)
        if run.status != "completed":
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Claims require a completed, frozen ResearchRun")
        if claim.schema_version != "claim-draft-v1":
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Unsupported ClaimDraft schema version")
        if not claim.claim_id.strip() or not claim.text.strip() or not claim.claim_type.strip():
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Claim ID, text, and type are required")
        evidence_ids = [item.strip() for item in claim.evidence_ids if item and item.strip()]
        if not evidence_ids or len(set(evidence_ids)) != len(evidence_ids):
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Claim must cite one or more unique EvidenceItem IDs")
        missing = [evidence_id for evidence_id in evidence_ids if self._missing_evidence(run.research_run_id, evidence_id)]
        if missing:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Claim cites evidence outside its ResearchRun", ", ".join(missing))
        normalized_claim = ClaimDraft(claim.claim_id.strip(), claim.research_run_id, claim.text.strip(), evidence_ids, claim.claim_type.strip(), claim.schema_version)
        return {"valid": True, "claim": normalized_claim.to_dict(), "normalized_evidence_ids": evidence_ids, "research_run_status": run.status, "validated_evidence_count": len(evidence_ids)}

    def _missing_evidence(self, research_run_id: str, evidence_id: str) -> bool:
        try:
            self.ledger.get_evidence_item(research_run_id, evidence_id)
        except McpAppError as exc:
            if exc.code == ErrorCode.NOT_FOUND:
                return True
            raise
        return False
