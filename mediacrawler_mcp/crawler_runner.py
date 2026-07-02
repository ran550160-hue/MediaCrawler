from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mediacrawler_mcp.errors import ErrorCode, McpAppError


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(slots=True)
class CollectionOptions:
    include_comments: bool = True
    max_contents: int = 20
    max_comments_per_content: int = 10
    include_sub_comments: bool = False
    headless: bool = True
    login_type: str = "qrcode"
    cookie_string: str | None = None
    enable_cdp_mode: bool = False
    cdp_connect_existing: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "include_comments": self.include_comments,
            "max_contents": self.max_contents,
            "max_comments_per_content": self.max_comments_per_content,
            "include_sub_comments": self.include_sub_comments,
            "headless": self.headless,
            "login_type": self.login_type,
            "cookie_present": bool(self.cookie_string),
            "enable_cdp_mode": self.enable_cdp_mode,
            "cdp_connect_existing": self.cdp_connect_existing,
        }


class CrawlerRunner:
    def __init__(self, repo_root: Path = REPO_ROOT):
        self.repo_root = repo_root

    def build_command(
        self,
        keywords: list[str],
        output_dir: Path,
        options: CollectionOptions,
    ) -> list[str]:
        command = [
            sys.executable,
            "main.py",
            "--platform",
            "xhs",
            "--lt",
            options.login_type,
            "--type",
            "search",
            "--keywords",
            ",".join(keywords),
            "--save_data_option",
            "jsonl",
            "--save_data_path",
            str(output_dir),
            "--crawler_max_notes_count",
            str(options.max_contents),
            "--max_comments_count_singlenotes",
            str(options.max_comments_per_content),
            "--get_comment",
            str(options.include_comments).lower(),
            "--get_sub_comment",
            str(options.include_sub_comments).lower(),
            "--headless",
            str(options.headless).lower(),
            "--enable_cdp_mode",
            str(options.enable_cdp_mode).lower(),
            "--cdp_connect_existing",
            str(options.cdp_connect_existing).lower(),
        ]
        if options.cookie_string:
            command.extend(["--cookies", options.cookie_string])
        return command

    def start(
        self,
        keywords: list[str],
        output_dir: Path,
        log_path: Path,
        options: CollectionOptions,
    ) -> subprocess.Popen:
        output_dir.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command = self.build_command(keywords=keywords, output_dir=output_dir, options=options)
        log_file = log_path.open("ab")
        try:
            return subprocess.Popen(
                command,
                cwd=self.repo_root,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        except Exception:
            log_file.close()
            raise

    def archive_outputs(self, output_dir: Path, raw_dir: Path, max_contents: int | None = None) -> dict[str, str]:
        raw_dir.mkdir(parents=True, exist_ok=True)
        jsonl_dir = output_dir / "xhs" / "jsonl"
        if not jsonl_dir.exists():
            raise McpAppError(
                ErrorCode.CRAWLER_FAILED,
                "Crawler did not produce xhs jsonl output",
                f"Missing output directory: {jsonl_dir}",
            )

        archived: dict[str, str] = {}
        selected_content_ids: set[str] | None = None
        for item_type, target_name in (("contents", "xhs_contents.jsonl"), ("comments", "xhs_comments.jsonl")):
            candidates = sorted(
                jsonl_dir.glob(f"search_{item_type}_*.jsonl"),
                key=lambda path: (path.stat().st_mtime, path.name),
                reverse=True,
            )
            if not candidates:
                continue
            target = raw_dir / target_name
            if item_type == "contents" and max_contents:
                selected_content_ids = self._copy_limited_contents(candidates[0], target, max_contents)
            elif item_type == "comments" and selected_content_ids is not None:
                self._copy_comments_for_contents(candidates[0], target, selected_content_ids)
            else:
                shutil.copyfile(candidates[0], target)
            archived[item_type] = str(target)
        if not archived:
            raise McpAppError(
                ErrorCode.CRAWLER_FAILED,
                "Crawler did not produce contents or comments JSONL files",
                f"Output directory: {jsonl_dir}",
            )
        return archived

    @staticmethod
    def _copy_limited_contents(source: Path, target: Path, max_contents: int) -> set[str]:
        selected_ids: set[str] = set()
        written = 0
        with source.open("r", encoding="utf-8") as src, target.open("w", encoding="utf-8") as dst:
            for line in src:
                if written >= max_contents:
                    break
                if not line.strip():
                    continue
                dst.write(line)
                written += 1
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                content_id = str(item.get("note_id") or "").strip()
                if content_id:
                    selected_ids.add(content_id)
        return selected_ids

    @staticmethod
    def _copy_comments_for_contents(source: Path, target: Path, content_ids: set[str]) -> None:
        with source.open("r", encoding="utf-8") as src, target.open("w", encoding="utf-8") as dst:
            for line in src:
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if str(item.get("note_id") or "").strip() in content_ids:
                    dst.write(line)
