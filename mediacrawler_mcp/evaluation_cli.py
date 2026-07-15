from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mediacrawler_mcp.config import load_config
from mediacrawler_mcp.errors import McpAppError
from mediacrawler_mcp.evaluation_orchestrator import EvaluationOrchestrator
from mediacrawler_mcp.storage import Storage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mediacrawler_mcp.evaluation_cli")
    subs = parser.add_subparsers(dest="command", required=True)
    prepare = subs.add_parser("prepare"); source = prepare.add_mutually_exclusive_group(required=True); source.add_argument("--dataset-id"); source.add_argument("--research-run-id"); prepare.add_argument("--card", type=Path, required=True); prepare.add_argument("--runs-root", type=Path, default=Path("evaluation/runs"))
    for name in ("validate", "evaluate", "status"):
        item = subs.add_parser(name); item.add_argument("--evaluation-dir", type=Path, required=True)
        if name == "evaluate": item.add_argument("--mode", choices=("provisional", "formal"), required=True)
        if name == "validate": item.add_argument("--formal", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config(); orchestrator = EvaluationOrchestrator(Storage(config), getattr(args, "runs_root", Path("evaluation/runs")))
        if args.command == "prepare": result = orchestrator.prepare(json.loads(args.card.read_text(encoding="utf-8")), dataset_id=args.dataset_id, research_run_id=args.research_run_id)
        elif args.command == "validate": result = orchestrator.validate(args.evaluation_dir, formal=args.formal)
        elif args.command == "evaluate": result = orchestrator.evaluate(args.evaluation_dir, args.mode)
        else: result = orchestrator.status(args.evaluation_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)); return 0
    except (McpAppError, ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"evaluation error: {exc}", file=sys.stderr); return 3


if __name__ == "__main__":
    raise SystemExit(main())
