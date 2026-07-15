# Claim Draft schema and deterministic validation

`ClaimDraft` is a future, human-authored proposal with `claim_id`,
`research_run_id`, `text`, `claim_type`, `evidence_ids`, and
`schema_version=claim-draft-v1`.

`ClaimValidator` is read-only. It accepts only a non-empty draft that cites one
or more unique EvidenceItem IDs from the same completed, frozen ResearchRun.
It does not persist a claim, create prose, rank evidence, call an LLM, or turn
pilot analysis into a research conclusion.
