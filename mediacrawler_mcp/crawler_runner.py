from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mediacrawler_mcp.dataset_importer import RAW_FILE_NAMES
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
    cdp_debug_port: int | None = None

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
            "cdp_debug_port": self.cdp_debug_port,
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
        if options.cdp_debug_port:
            command.extend(["--cdp_debug_port", str(options.cdp_debug_port)])
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

        archived: dict[str, str] = {}
        selected_content_ids: set[str] | None = None
        discovery = self._discover_run_layout(output_dir, platform="xhs")
        layout = discovery["output_layout"]
        run_id = discovery.get("run_id")
        contents_source = discovery["contents_source"]
        comments_source = discovery["comments_source"]
        raw_names = RAW_FILE_NAMES["xhs"]

        if contents_source:
            target = raw_dir / raw_names["contents"]
            if max_contents:
                selected_content_ids = self._copy_limited_contents(contents_source, target, max_contents)
            else:
                shutil.copyfile(contents_source, target)
                selected_content_ids = self._collect_content_ids(contents_source)
            archived["contents"] = str(target)
        if comments_source:
            target = raw_dir / raw_names["comments"]
            if selected_content_ids is not None:
                self._copy_comments_for_contents(comments_source, target, selected_content_ids)
            else:
                shutil.copyfile(comments_source, target)
            archived["comments"] = str(target)
        if not archived:
            raise McpAppError(
                ErrorCode.CRAWLER_FAILED,
                "Crawler did not produce contents or comments JSONL files",
               f"Output directory: {jsonl_dir}",
            )
        archived["output_layout"] = layout
        archived["run_id"] = run_id or ""
        return archived

    def _discover_run_layout(self, output_dir: Path, platform: str) -> dict[str, Any]:
        """Discover the contents/comments layout for a crawler run output.

        Prefers the run-isolated layout (``<output_dir>/<platform>/<run_id>/...``)
        and selects a single run directory deterministically. Falls back to the
        legacy shared ``<output_dir>/<platform>/jsonl/search_*_YYYY-MM-DD.jsonl``
        layout only when no run directory exists.
        """
        platform_dir = output_dir / platform
        if not platform_dir.exists():
            raise McpAppError(
                ErrorCode.CRAWLER_FAILED,
                f"Crawler did not produce {platform} output",
                f"Missing output directory: {platform_dir}",
            )

        run_dirs = [
            child
            for child in platform_dir.iterdir()
            if child.is_dir() and (child / "run_metadata.json").exists()
        ]
        # Keep only run directories that actually contain a usable contents or
        # comments file, so stale empty runs from prior failed tasks do not
        # create ambiguous choices.
        usable_run_dirs = [
            run for run in run_dirs
            if (run / "jsonl" / "search_contents.jsonl").exists()
            or (run / "jsonl" / "search_comments.jsonl").exists()
        ]

        if usable_run_dirs:
            chosen = self._select_run_directory(usable_run_dirs)
            return {
                "output_layout": "run_isolated",
                "run_id": chosen.name,
                "contents_source": chosen / "jsonl" / "search_contents.jsonl",
                "comments_source": chosen / "jsonl" / "search_comments.jsonl",
            }

        jsonl_dir = platform_dir / "jsonl"
        contents_source = self._latest_legacy_file(jsonl_dir, "contents")
        comments_source = self._latest_legacy_file(jsonl_dir, "comments")
        if contents_source is None and comments_source is None:
            raise McpAppError(
                ErrorCode.CRAWLER_FAILED,
                f"Crawler did not produce {platform} jsonl output",
                f"Missing output directory: {jsonl_dir}",
            )
        return {
            "output_layout": "legacy_shared",
            "run_id": None,
            "contents_source": contents_source,
            "comments_source": comments_source,
        }

    @staticmethod
    def _select_run_directory(run_dirs: list[Path]) -> Path:
        if len(run_dirs) == 1:
            return run_dirs[0]
        # Prefer a single run directory with actual contents data; if more than
        # one has contents, this is ambiguous and we refuse to silently merge.
        with_contents = [run for run in run_dirs if (run / "jsonl" / "search_contents.jsonl").exists()]
        if len(with_contents) == 1:
            return with_contents[0]
        # As a last resort, fall back to the most recently modified run, but only
        # if no two runs share the same mtime second (which would be ambiguous).
        newest = max(run_dirs, key=lambda path: (path.stat().st_mtime, path.name))
        tied = [run for run in run_dirs if abs(run.stat().st_mtime - newest.stat().st_mtime) < 1.0]
        if len(tied) > 1:
            raise McpAppError(
                ErrorCode.CRAWLER_FAILED,
                "Multiple crawler runs detected; refusing to silently merge",
                "Found run directories: " + ", ".join(sorted(run.name for run in run_dirs)),
            )
        return newest

    @staticmethod
    def _latest_legacy_file(jsonl_dir: Path, item_type: str) -> Path | None:
        if not jsonl_dir.exists():
            return None
        candidates = sorted(
            jsonl_dir.glob(f"search_{item_type}_*.jsonl"),
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
        return candidates[0] if candidates else None

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

    @staticmethod
    def _collect_content_ids(source: Path) -> set[str]:
        ids: set[str] = set()
        with source.open("r", encoding="utf-8") as src:
            for line in src:
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                content_id = str(item.get("note_id") or "").strip()
                if content_id:
                    ids.add(content_id)
        return ids
