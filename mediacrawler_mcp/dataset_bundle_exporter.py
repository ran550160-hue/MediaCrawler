from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from analysis.loaders import AnalysisDataError, load_dataframe, resolve_input_files
from mediacrawler_mcp.dataset_importer import RAW_FILE_NAMES
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.utils import make_dataset_id, utc_now_iso


class DatasetBundleExporter:
    def export_xhs_bundle(
        self,
        name: str,
        output_dir: str | Path,
        keywords: list[str],
        description: str | None = None,
        data_root: str | Path = "data",
        crawler_type: str = "search",
        contents_path: str | Path | None = None,
        comments_path: str | Path | None = None,
        dataset_id: str | None = None,
        collection_task_id: str | None = None,
        collection_started_at: str | None = None,
        collection_completed_at: str | None = None,
    ) -> dict[str, Any]:
        name = (name or "").strip()
        keywords = [keyword.strip() for keyword in keywords or [] if keyword and keyword.strip()]
        crawler_type = (crawler_type or "search").strip()
        if not name:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Dataset name is required")
        if not keywords:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "At least one keyword is required")
        if not crawler_type:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Crawler type is required")

        output_root = Path(output_dir).expanduser().resolve()
        dataset_id = self._normalize_dataset_id(dataset_id) if dataset_id else make_dataset_id(name, output_root)
        bundle_dir = output_root / dataset_id
        if bundle_dir.exists():
            raise McpAppError(
                ErrorCode.INVALID_ARGUMENT,
                "Dataset bundle already exists",
                f"dataset_dir={bundle_dir}",
            )

        contents_source, comments_source = resolve_input_files(
            platform="xhs",
            crawler_type=crawler_type,
            contents=contents_path,
            comments=comments_path,
            data_root=data_root,
        )
        if not contents_source and not comments_source:
            raise McpAppError(
                ErrorCode.INVALID_ARGUMENT,
                "No xhs input files found",
                f"Expected xhs {crawler_type}_contents or {crawler_type}_comments under {data_root}",
            )

        raw_dir = bundle_dir / "raw"
        media_dir = bundle_dir / "media"
        logs_dir = bundle_dir / "logs"
        raw_dir.mkdir(parents=True)
        media_dir.mkdir()
        logs_dir.mkdir()

        warnings: list[str] = []
        errors: list[str] = []
        files: dict[str, Any] = {"raw": {}, "reports": {}}
        metrics = {"content_count": 0, "comment_count": 0}
        raw_names = RAW_FILE_NAMES["xhs"]

        if contents_source:
            destination = raw_dir / raw_names["contents"]
            metrics["content_count"] = self._copy_as_jsonl(contents_source, destination)
            files["raw"]["contents"] = self._raw_file_info("xhs", destination, metrics["content_count"])
        else:
            warnings.append("contents input is missing; bundle will contain comments only")

        if comments_source:
            destination = raw_dir / raw_names["comments"]
            metrics["comment_count"] = self._copy_as_jsonl(comments_source, destination)
            files["raw"]["comments"] = self._raw_file_info("xhs", destination, metrics["comment_count"])
        else:
            warnings.append("comments input is missing; comment analysis will be empty")

        now = utc_now_iso()
        manifest = {
            "dataset_id": dataset_id,
            "name": name,
            "description": description,
            "status": "exported",
            "platforms": ["xhs"],
            "keywords": keywords,
            "options": {
                "source": "desktop_export",
                "crawler_type": crawler_type,
                "data_root": str(Path(data_root).expanduser()),
                "contents_source": str(contents_source) if contents_source else None,
                "comments_source": str(comments_source) if comments_source else None,
                "collection_task_id": collection_task_id or None,
                "collection_started_at": collection_started_at or None,
                "collection_completed_at": collection_completed_at or None,
            },
            "dataset_dir": str(bundle_dir),
            "created_at": now,
            "updated_at": now,
            "files": files,
            "metrics": metrics,
            "warnings": warnings,
            "errors": errors,
        }
        (bundle_dir / "dataset.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "dataset_id": dataset_id,
            "dataset_dir": str(bundle_dir),
            "dataset_json_path": str(bundle_dir / "dataset.json"),
            "raw_files": files["raw"],
            "metrics": metrics,
            "warnings": warnings,
        }

    @staticmethod
    def _copy_as_jsonl(source: Path | str, destination: Path) -> int:
        try:
            dataframe = load_dataframe(source)
        except AnalysisDataError as exc:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Failed to load input data", str(exc)) from exc

        rows = dataframe.to_dict("records")
        with destination.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(DatasetBundleExporter._clean_record(row), ensure_ascii=False) + "\n")
        return len(rows)

    @staticmethod
    def _clean_record(row: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, float) and math.isnan(value):
                clean[key] = None
            else:
                clean[key] = value
        return clean

    @staticmethod
    def _raw_file_info(platform: str, path: Path, line_count: int) -> dict[str, Any]:
        return {
            "platform": platform,
            "path": str(path),
            "line_count": line_count,
            "size_bytes": path.stat().st_size,
        }

    @staticmethod
    def _normalize_dataset_id(value: str | None) -> str:
        if value is None:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Dataset ID is required")
        normalized = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in value.strip())
        normalized = normalized.strip("._-")
        if not normalized:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Invalid dataset_id")
        return normalized
