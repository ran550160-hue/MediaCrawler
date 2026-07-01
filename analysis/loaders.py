from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd


SUPPORTED_SUFFIXES = {".jsonl", ".json", ".csv"}
DATA_SUBDIRS = ("jsonl", "json", "csv")
PLATFORM_DATA_DIRS = {
    "dy": "douyin",
}


class AnalysisDataError(ValueError):
    """Raised when analysis input data cannot be loaded."""


def _iter_candidate_files(
    data_root: Path,
    platform: str,
    crawler_type: str,
    item_type: str,
) -> Iterable[Path]:
    platform_dir = PLATFORM_DATA_DIRS.get(platform, platform)
    for subdir in DATA_SUBDIRS:
        base_dir = data_root / platform_dir / subdir
        if not base_dir.exists():
            continue
        pattern = f"{crawler_type}_{item_type}_*{'.' + subdir}"
        yield from base_dir.glob(pattern)


def discover_latest_data_file(
    platform: str,
    crawler_type: str,
    item_type: str,
    data_root: Path | str = "data",
) -> Optional[Path]:
    """Find the newest MediaCrawler output file for a platform and item type."""

    root = Path(data_root)
    candidates = [
        path
        for path in _iter_candidate_files(root, platform, crawler_type, item_type)
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def load_dataframe(file_path: Path | str) -> pd.DataFrame:
    """Load a JSONL, JSON, or CSV output file into a DataFrame."""

    path = Path(file_path)
    if not path.exists():
        raise AnalysisDataError(f"Input file does not exist: {path}")
    if not path.is_file():
        raise AnalysisDataError(f"Input path is not a file: {path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise AnalysisDataError(f"Unsupported input file type: {suffix}. Supported: {supported}")

    if suffix == ".jsonl":
        rows = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AnalysisDataError(f"Invalid JSONL line in {path}: {exc}") from exc
                if isinstance(value, dict):
                    rows.append(value)
                else:
                    raise AnalysisDataError(f"JSONL rows must be objects: {path}")
        return pd.DataFrame(rows)

    if suffix == ".json":
        with path.open("r", encoding="utf-8") as f:
            try:
                value = json.load(f)
            except json.JSONDecodeError as exc:
                raise AnalysisDataError(f"Invalid JSON file {path}: {exc}") from exc
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            raise AnalysisDataError(f"JSON input must contain an object or array: {path}")
        return pd.DataFrame(value)

    return pd.read_csv(path, dtype=object, keep_default_na=False)


def resolve_input_files(
    platform: str,
    crawler_type: str,
    contents: Path | str | None = None,
    comments: Path | str | None = None,
    data_root: Path | str = "data",
) -> tuple[Optional[Path], Optional[Path]]:
    """Resolve explicit or auto-discovered contents and comments files."""

    contents_path = Path(contents) if contents else discover_latest_data_file(
        platform=platform,
        crawler_type=crawler_type,
        item_type="contents",
        data_root=data_root,
    )
    comments_path = Path(comments) if comments else discover_latest_data_file(
        platform=platform,
        crawler_type=crawler_type,
        item_type="comments",
        data_root=data_root,
    )
    return contents_path, comments_path
