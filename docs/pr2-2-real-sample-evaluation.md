# PR2.2 provisional pilot evaluation

Status: **evaluation tooling complete; real-sample formal acceptance pending**.
This pilot is not a Claim gate and is not a final effectiveness acceptance.

## Frozen pilot snapshots

| Dataset | ResearchRun | Evidence-set fingerprint | Population/sample | Method |
| --- | --- | --- | ---: | --- |
| `ds_20260707_221719_ai` | `research_c0cbeee03fb64103` | `0ad8fb2e…bb1c0f55` | 25 / 25 | census |
| `ds_20260711_132420_2026_07_11` | `research_250386e65173442e` | `0a2700cd…8f08acb3` | 20 / 20 | census |
| `ds_20260711_211940_cursor` | `research_60ca818ed9034de0` | `8033d623…4a76e233` | 20 / 20 | census |

The full IDs, stratification, seed, and selected evidence IDs are saved in
`evaluation/pilot/artifacts/*/sampling_manifest.json`. The run state is frozen
in the pilot runtime; rebuilding from a changed source snapshot is rejected.

PR2.3 duplicate coverage: exact/body-only each inspected the full 25/20/20
EvidenceItems; near inspected 2/20/0 EvidenceItems under
`duplicate_input_scope=included_uncertain`. Candidate edges are respectively
exact `0/0/0`, body-only `0/0/0`, and near `0/0/0`. This is still a
provisional zero-candidate observation, not duplicate-recall evidence.

## Pilot results

| Dataset | Included precision / recall | TP / FP / FN / TN | Candidate recall | False-exclusion rate | Main observation |
| --- | ---: | ---: | ---: | ---: | --- |
| AI export | 1.000 / 0.400 | 2 / 0 / 3 / 20 | 0.400 | 0.600 | Manifest says AI tools while most collected content is World Cup material; direct AI wording is a high-precision but low-recall rule. |
| Hot-topic export | 0.714 / 0.714 | 5 / 2 / 2 / 11 | 1.000 | 0.000 | Literal “热点” incorrectly includes Wi-Fi-hotspot jokes; current-event relevance without the literal term becomes uncertain. |
| Cursor export | 0.000 / 0.000 | 0 / 0 / 11 / 9 | 0.000 | 1.000 | Research keyword `Cursor编辑器` does not match common English `Cursor` text, producing blanket false exclusions. |

All reports contain Wilson 95% intervals. They are descriptive only: samples are
small, corpus-specific, and the provisional labels were recorded as
`codex_provisional_manual_review`, not independent human adjudication.

Duplicate analysis predicted no exact or near candidate edges in all three
snapshots. Ten blind control pairs per snapshot were labeled `not_duplicate`.
Thus exact confirmation is not applicable, predicted-but-unannotated is zero,
and duplicate recall is not established; a zero-candidate result cannot prove
duplicate detection effectiveness.

## Reproducible workflow

```powershell
.venv\Scripts\python.exe scripts\run_provisional_pilot_evaluation.py
# Review the blind JSONL templates; they contain no prediction fields.
.venv\Scripts\python.exe scripts\complete_provisional_pilot_annotations.py
.venv\Scripts\python.exe scripts\run_provisional_pilot_evaluation.py --evaluate
```

`EvaluationWorkflow` exports a manifest plus blind relevance and pair JSONL,
then imports and validates completed files against ResearchRun and
evidence-set fingerprint before writing JSON and Markdown reports. The sample
annotation script records this repository's transparent provisional labels; it
does not replace the independent human labels required for formal acceptance.

## Formal-acceptance next step

Collect or register a new 30–50 content snapshot for each question, create a
new frozen ResearchRun, independently annotate relevance and blind duplicate
pairs, and rerun the same workflow. Only that new evaluation snapshot may be
used to decide whether relevance or duplicate algorithms should change.
