from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def timestamp_for_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return slug[:48] or "dataset"


def make_dataset_id(name: str, datasets_dir: Path) -> str:
    base = f"ds_{timestamp_for_id()}_{slugify(name)}"
    candidate = base
    while (datasets_dir / candidate).exists():
        candidate = f"{base}_{secrets.token_hex(2)}"
    return candidate


def make_report_id(report_type: str) -> str:
    return f"report_{timestamp_for_id()}_{slugify(report_type)}"


def make_task_id(task_type: str) -> str:
    return f"task_{slugify(task_type)}_{timestamp_for_id()}_{secrets.token_hex(2)}"


def setup_file_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if any(isinstance(handler, logging.FileHandler) and handler.baseFilename == str(log_path) for handler in root.handlers):
        return

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
