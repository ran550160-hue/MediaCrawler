# PR2.5 generic evaluation CLI

Evaluation cards use `evaluation-card-v1` and reject unknown or missing fields:

```json
{
  "schema_version": "evaluation-card-v1",
  "name": "cursor-formal-202607",
  "research_question": "How do users evaluate Cursor editor onboarding?",
  "platforms": ["xhs"],
  "keywords": ["Cursor", "Cursor编辑器"],
  "sampling": {"sample_size": 40, "seed": 20260714, "strategy": "stratified_random_without_replacement"},
  "analysis": {"duplicate_input_scope": "included_uncertain", "near_duplicate_threshold": 0.85},
  "annotation": {"mode": "independent_blind", "duplicate_gold_completeness": "complete"},
  "quality_gate": {"min_included_precision": 0.8, "min_included_recall": 0.7, "max_false_exclusion_rate": 0.1}
}
```

Run from the repository root:

```powershell
python -m mediacrawler_mcp.evaluation_cli prepare --dataset-id <dataset_id> --card card.json
python -m mediacrawler_mcp.evaluation_cli validate --evaluation-dir evaluation/runs/<evaluation_id>
python -m mediacrawler_mcp.evaluation_cli evaluate --evaluation-dir evaluation/runs/<evaluation_id> --mode formal
python -m mediacrawler_mcp.evaluation_cli status --evaluation-dir evaluation/runs/<evaluation_id>
```

`prepare` creates a non-overwritable `evaluation/runs/<evaluation_id>/` directory
with card, evaluation metadata, manifest, and blind templates. A completed
annotation file must be named `relevance_annotations.completed.jsonl` or
`duplicate_pair_annotations.completed.jsonl`; CLI never writes either file.

Formal evaluation requires complete manifest matching, annotation provenance,
and `duplicate_gold_completeness=complete`. Its status is separate from the
quality gate: `formal_evaluation_complete` means the process completed;
`quality_gate_passed`, `quality_gate_not_met`, and `quality_gate_not_evaluable`
describe only the configured quality thresholds.

MCP exposes only `prepare_evaluation`, `get_evaluation_status`, and
`get_evaluation_report`. It returns controlled summaries and never annotation
bodies or a tool that writes human labels.
