from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mediacrawler_mcp.dataset_bundle_exporter import DatasetBundleExporter
from mediacrawler_mcp.errors import McpAppError


def _parse_keywords(values: list[str] | None, csv_value: str | None) -> list[str]:
    keywords: list[str] = []
    for value in values or []:
        keywords.extend(part.strip() for part in value.split(",") if part.strip())
    if csv_value:
        keywords.extend(part.strip() for part in csv_value.split(",") if part.strip())
    return keywords


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export desktop MediaCrawler output into a dataset bundle for MediaCrawler MCP.",
    )
    parser.add_argument("--name", required=True, help="Dataset display name.")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory that will contain the dataset bundle.")
    parser.add_argument("--keyword", action="append", help="Dataset keyword. Can be repeated or comma-separated.")
    parser.add_argument("--keywords", help="Comma-separated dataset keywords.")
    parser.add_argument("--description", default=None, help="Optional dataset description.")
    parser.add_argument("--data-root", type=Path, default=Path("data"), help="MediaCrawler data root for auto-discovery.")
    parser.add_argument("--crawler-type", default="search", help="MediaCrawler crawler type prefix, default: search.")
    parser.add_argument("--contents", type=Path, default=None, help="Explicit contents input file.")
    parser.add_argument("--comments", type=Path, default=None, help="Explicit comments input file.")
    parser.add_argument("--dataset-id", default=None, help="Optional deterministic dataset_id.")
    args = parser.parse_args()

    try:
        result = DatasetBundleExporter().export_xhs_bundle(
            name=args.name,
            output_dir=args.output_dir,
            keywords=_parse_keywords(args.keyword, args.keywords),
            description=args.description,
            data_root=args.data_root,
            crawler_type=args.crawler_type,
            contents_path=args.contents,
            comments_path=args.comments,
            dataset_id=args.dataset_id,
        )
    except McpAppError as exc:
        print(json.dumps(exc.to_result(), ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(2) from exc

    print(json.dumps({"status": "success", **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
