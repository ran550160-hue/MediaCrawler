# PR2: relevance and duplicate candidates

This PR adds a read-only analysis module over completed Evidence Ledger runs.
It never changes `EvidenceItem`, generates no Claim, and makes no LLM call.

- Relevance uses `keyword-substring-relevance-v1`: each configured research
  keyword is searched verbatim (case-insensitively) in evidence title/body.
  The decision is `included` when one or more keywords match; reasons contain
  the matched terms or the explicit no-match reason.
- Exact candidates use an independent fingerprint of NFC-normalized title and
  full text, never `content_hash`.
- Near candidates use `exact-fingerprint-plus-type-aware-char-bigram-jaccard-v2`,
  Unicode-NFC/case-folded character bigrams, and a caller-recorded Jaccard
  threshold (default 0.85). It never compares different content types and
  skips text below 20 characters. Every candidate edge stores both evidence
  IDs, similarity, threshold, and algorithm version. Candidate groups are not
  automatic merges; their edges make chain expansion auditable.

Every relevance row and every duplicate-group member stores a concrete
`evidence_id`. An `analysis_run_id` records both algorithm versions and the
configuration, so results remain explainable and reproducible against the
frozen ResearchRun.
