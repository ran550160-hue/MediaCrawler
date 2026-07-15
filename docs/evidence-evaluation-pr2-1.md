# PR2.4 offline evaluation contract

Each relevance annotation is JSONL validated by
`tests/fixtures/evidence_evaluation/annotations.schema.json`. It is bound to
`research_question`, `research_run_id`, `evidence_set_fingerprint`, immutable
`evidence_id`, and schema version `evaluation-annotation-v2`. The evaluator
rejects an unknown ID, a different run/fingerprint, or contradictory labels for
the same evidence. This prevents an annotation set being silently reused after
an evidence-set change.

Near-duplicate truth is separate JSONL validated by
`duplicate_pair_annotations.schema.json`. `expected_relation` is `duplicate`,
`not_duplicate`, or `uncertain`. Near pairs are intentionally not inferred from
`expected_duplicate_group`: near similarity is not transitive. Exact duplicate
groups remain a compact annotation for exact-pair truth.

## Relevance metrics

For a label `L`, precision is `predicted L ∩ gold L / predicted L`; recall is
`predicted L ∩ gold L / gold L`. The report gives included precision/recall and
excluded precision. `uncertain_rate = predicted uncertain / all decisions`.
`decision_coverage = (predicted included + predicted excluded) / all decisions`.

For candidate filtering, candidate means `included ∪ uncertain`.
`candidate_precision` and `candidate_recall` use gold `included ∪ uncertain`.
`false_exclusion_rate` is gold candidates predicted excluded divided by all gold
candidates. The 3×3 matrix is indexed `gold label -> predicted label`.

## Duplicate fingerprint semantics

`exact_duplicate_fingerprint` is SHA-256 over a canonical JSON object with
`fingerprint_schema_version`, `content_type`, NFC-normalized/trimmed `title`,
and NFC-normalized/trimmed `full_text`; JSON uses sorted keys and compact
separators. It deliberately has no platform field, so it supports cross-platform
copy candidates of the same content type. A distinct body-only fingerprint uses
`content_type` plus full text and reports same-body/different-title candidates
separately. Text shorter than 20 normalized characters produces neither a
high-confidence exact group nor a near candidate.

Duplicate execution is versioned as `structured-exact-fingerprint-plus-type-aware-char-bigram-jaccard-v4`; sampling manifests are `evaluation-sampling-v2`; evaluation reports are `evaluation-report-v2`. Older manifests remain readable artifacts but are rejected as current formal-acceptance inputs.

`content_hash` remains the immutable EvidenceItem revision identity; it is not a
cross-entity duplicate key.

## Scope and current acceptance status

The repository's checked-in real exports contain at most 25 normalized content
rows per dataset. Ledger currently builds from contents, not comments, so there
are no three real ResearchRuns with 30–50 EvidenceItems each and no supplied
human labels. PR2.2 therefore provides the reproducible annotation contract and
reports the Claim gate as **not met** until three human-reviewed 30–50 item
samples are supplied/built. Synthetic edge fixtures exercise only the framework;
they are not reported as real-data effectiveness.
