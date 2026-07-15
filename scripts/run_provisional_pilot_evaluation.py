"""Build reproducible, frozen pilot snapshots from the three checked-in real exports.

The script intentionally exports blind templates only. A reviewer fills them,
then runs it again with ``--evaluate`` to validate and write JSON/Markdown.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.dataset_importer import DatasetImporter
from mediacrawler_mcp.evaluation_workflow import EvaluationWorkflow
from mediacrawler_mcp.evidence_analysis import EvidenceAnalysis
from mediacrawler_mcp.evidence_ledger import EvidenceLedger
from mediacrawler_mcp.storage import Storage


PILOTS = (
    ("ds_20260707_221719_ai", "What evidence in this exported snapshot is relevant to AI tools?"),
    ("ds_20260711_132420_2026_07_11", "What evidence in this exported snapshot is relevant to Xiaohongshu hot topics?"),
    ("ds_20260711_211940_cursor", "What evidence in this exported snapshot is relevant to Cursor editor research?"),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluate", action="store_true", help="Validate completed annotations and write reports")
    parser.add_argument("--root", type=Path, default=REPO_ROOT / "evaluation" / "pilot")
    args = parser.parse_args()
    root: Path = args.root
    runtime = root / "runtime"
    artifacts = root / "artifacts"
    config = McpConfig(runtime, "persistent_context", None, 1, 300)
    storage = Storage(config)
    importer, ledger = DatasetImporter(config, storage), EvidenceLedger(storage)
    workflow = EvaluationWorkflow(EvidenceAnalysis(storage))
    index_path = artifacts / "pilot_runs.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    for dataset_id, question in PILOTS:
        entry = index.get(dataset_id)
        if entry is None:
            imported = importer.register_dataset(str(REPO_ROOT / "datasets" / dataset_id), import_mode="copy")
            manifest = json.loads((REPO_ROOT / "datasets" / dataset_id / "dataset.json").read_text(encoding="utf-8"))
            run = ledger.create_research_run(question, manifest["platforms"], manifest.get("keywords", []), sample_limits={"pilot": True})
            built = ledger.build_evidence_items(run.research_run_id, imported["dataset_id"])
            analysis = EvidenceAnalysis(storage).analyze(run.research_run_id)
            entry = {"research_run_id": run.research_run_id, "analysis_run_id": analysis["analysis_run_id"], "evidence_set_fingerprint": analysis["evidence_set_fingerprint"], "evidence_count": built["evidence_count"]}
            index[dataset_id] = entry
            artifacts.mkdir(parents=True, exist_ok=True)
            index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        elif not args.evaluate:
            analysis = EvidenceAnalysis(storage).analyze(entry["research_run_id"])
            entry["analysis_run_id"] = analysis["analysis_run_id"]
            entry["evidence_set_fingerprint"] = analysis["evidence_set_fingerprint"]
            index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        output = artifacts / dataset_id
        if args.evaluate:
            relevance_path = output / "relevance_annotations.completed.jsonl"
            pair_path = output / "duplicate_pair_annotations.completed.jsonl"
            if not relevance_path.exists() or not pair_path.exists():
                raise SystemExit(f"Completed annotation files are required before evaluation: {output}")
            workflow.import_and_evaluate(entry["analysis_run_id"], relevance_path, pair_path, output, provisional=True)
        else:
            workflow.export_annotation_bundle(entry["analysis_run_id"], output, sample_size=entry["evidence_count"], seed=20260713)
    print(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
