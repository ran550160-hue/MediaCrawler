# Evidence Ledger PR1.1: immutable versions

## Identity and lifecycle

An `EvidenceItem` is immutable. Its ID is derived from:

```text
evidence_id = sha256(research_run_id, source_entity_id, content_hash)
source_entity_id = sha256(platform, content_type, content_id)
content_hash = sha256(canonical(title, full_text))
```

Thus one platform content entity can have several evidence versions, and a
future Claim cites the exact immutable `evidence_id`. The current PR stores
versions in `evidence_item_versions`; the original MVP `evidence_items` table
is retained untouched for backward SQLite compatibility.

For comments, `source_entity_id` additionally includes `parent_content_id` and,
for replies, `parent_comment_id`; a note, video, root comment, and reply cannot
collide merely because their local IDs match. `content_revision_id` is the
cross-run aggregation identity `sha256(source_entity_id, content_hash,
hash_algorithm_version)`. `list_evidence_versions` returns **research-run
instances** of that entity (each has its own `evidence_id`) and includes the
shared revision ID so callers can group them.

`ResearchRun` is frozen when its build reaches `completed`. Repeating the same
build with the same dataset version returns `already_completed`; a changed
source version is rejected and requires a new ResearchRun. A build first moves
to `building`, then atomically inserts evidence, discoveries, observations, and
the final `completed` state. On an exception it is `build_failed` and no
partial V2 ledger is committed. Source rows removed later do not change a
completed ledger.

The build lease is an atomic SQLite compare-and-set from `created` or
`build_failed` to `building`; a second caller receives an in-progress error.
An unexpectedly terminated process leaves `building`; an operator must call
`recover_evidence_ledger_build` to move it to `build_failed`, then retry.
DuckDB reads run in one read transaction. The dataset fingerprint is checked
both before and after reading DuckDB/raw inputs, so a changed input fails the
build instead of producing a mixed snapshot.

Each completed run records `source_dataset_id`, a SHA-256 `dataset_version` of
`analysis.duckdb` plus every cited raw JSONL input, `normalization_version`,
and `ledger_schema_version`.

## Hash semantics

- `raw_record_hash`: SHA-256 of every field in the raw JSON object. It changes
  for any raw observation change, including interaction counts or discovery
  fields.
- `content_hash`: SHA-256 of only canonical `title` and `full_text`; it is the
  semantic content version used in evidence identity.
- `canonical_content_hash`: SHA-256 of source entity identity, platform,
  content type, author ID, source URL, published timestamp, and canonical
  title/full text. It is a stable integrity fingerprint, not the version ID.
- Stable JSON serialization recursively Unicode-NFC-normalizes string keys and
  values, sorts object keys, uses UTF-8, `ensure_ascii=false`, and separators
`,` and `:`. Canonical text converts CRLF/CR to LF and trims outer whitespace.

The persisted `hash_algorithm_version` is
`sha256-canonical-json-nfc-v1`. It is stored on both the ResearchRun and every
EvidenceItem; a future rule change must introduce a new version string rather
than silently reusing old identities. The content canonicalization is
deliberately not an aggressive near-duplicate normalizer: it does not lowercase,
strip punctuation, remove stop words, or collapse internal whitespace.

Interaction counts are excluded from `content_hash` and
`canonical_content_hash`. They are kept in immutable `evidence_observations`,
so a like-only update does not create a semantic evidence version.

## Discovery and portable provenance

Multiple discovery paths are association rows, not a concatenated keyword:

```json
{
  "keyword": "AI编程",
  "collection_task_id": "task_a",
  "discovered_at": "2026-07-02T10:00:00+00:00",
  "result_position": 1
}
```

The primary raw record reference is portable and never stores a Windows
absolute path:

```json
{
  "dataset_id": "ds_example",
  "collection_task_id": "task_a",
  "dataset_relative_raw_path": "raw/xhs_contents.jsonl",
  "platform_record_id_field": "note_id",
  "platform_record_id": "xhs-evidence-001",
  "raw_record_hash": "sha256..."
}
```

Resolve it against the current datasets root with
`EvidenceLedger.resolve_raw_record_path`.

## MCP reading contract

`list_evidence_items` is paginated (`limit` defaults to 20 and caps at 100),
supports `platform` and `content_type`, returns compact summaries, and truncates
`full_text` to 1,000 characters. It never returns `metadata.raw`. Use
`get_evidence_item(research_run_id, evidence_id)` for one complete version,
including platform raw fields, discovery contexts, and observation snapshots.
`list_evidence_versions(platform, content_type, content_id)` queries all
immutable versions of the same source entity across research runs.

Claim generation remains intentionally unimplemented. The reserved
`ClaimGenerator` interface receives immutable evidence and must cite
`evidence_id`.
