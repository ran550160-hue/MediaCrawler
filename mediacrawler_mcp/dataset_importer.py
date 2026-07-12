from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.dataset_service import SUPPORTED_PLATFORMS
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.models import Dataset
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.utils import make_dataset_id, utc_now_iso


RAW_FILE_NAMES = {
    "xhs": {
        "contents": "xhs_contents.jsonl",
        "comments": "xhs_comments.jsonl",
    },
    "douyin": {
        "contents": "douyin_contents.jsonl",
        "comments": "douyin_comments.jsonl",
    },
}


class DatasetImporter:
    def __init__(self, config: McpConfig, storage: Storage):
        self.config = config
        self.storage = storage

    def validate_dataset_bundle(self, dataset_dir: str) -> dict[str, Any]:
        warnings: list[str] = []
        errors: list[str] = []
        raw_files: dict[str, Any] = {}
        manifest: dict[str, Any] = {}

        if not dataset_dir or not str(dataset_dir).strip():
            return {
                "valid": False,
                "dataset_dir": dataset_dir,
                "dataset_json_path": None,
                "raw_files": raw_files,
                "metadata": {},
                "warnings": warnings,
                "errors": ["dataset_dir is required"],
            }
        source_dir = Path(dataset_dir).expanduser()
        if not source_dir.exists():
            return {
                "valid": False,
                "dataset_dir": str(source_dir),
                "dataset_json_path": None,
                "raw_files": raw_files,
                "metadata": {},
                "warnings": warnings,
                "errors": [f"Dataset directory does not exist: {source_dir}"],
            }
        if not source_dir.is_dir():
            return {
                "valid": False,
                "dataset_dir": str(source_dir),
                "dataset_json_path": None,
                "raw_files": raw_files,
                "metadata": {},
                "warnings": warnings,
                "errors": [f"Dataset path is not a directory: {source_dir}"],
            }

        dataset_json_path = source_dir / "dataset.json"
        if dataset_json_path.exists():
            try:
                loaded = json.loads(dataset_json_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"Invalid dataset.json: {exc}")
            else:
                if isinstance(loaded, dict):
                    manifest = loaded
                else:
                    errors.append("dataset.json must contain a JSON object")
        else:
            warnings.append("dataset.json is missing; metadata will be inferred from directory and raw files")

        detected_platforms: set[str] = set()
        manifest_platforms = self._as_string_list(manifest.get("platforms"))
        # A bundle is single-platform; scope raw file detection to the declared
        # platform when available so the xhs and douyin file checks do not
        # overwrite each other's status under the same raw_files key.
        candidate_platforms = sorted(
            manifest_platforms or [*RAW_FILE_NAMES.keys()]
        )
        raw_dir = source_dir / "raw"
        for platform in candidate_platforms:
            names = RAW_FILE_NAMES.get(platform)
            if not names:
                continue
            for logical_name, file_name in names.items():
                path = raw_dir / file_name
                info = self._inspect_jsonl(path)
                raw_files[logical_name] = {
                    "path": str(path),
                    "exists": path.exists(),
                    "platform": platform,
                    **info,
                }
                if path.exists():
                    detected_platforms.add(platform)
                errors.extend(info["errors"])
            if manifest_platforms:
                break

        if not raw_files["contents"]["exists"] and not raw_files["comments"]["exists"]:
            if "contents" not in raw_files:
                raw_files["contents"] = {"path": "", "exists": False, "line_count": 0, "size_bytes": 0, "errors": []}
            if "comments" not in raw_files:
                raw_files["comments"] = {"path": "", "exists": False, "line_count": 0, "size_bytes": 0, "errors": []}
            expected = sorted(
                name for names in RAW_FILE_NAMES.values() for name in (names["contents"], names["comments"])
            )
            errors.append(f"No raw JSONL files found; expected one of {expected}")
        if not raw_files["comments"]["exists"]:
            warnings.append("raw comments file is missing; dataset can still be normalized with contents only")

        platforms = manifest_platforms or sorted(detected_platforms) or ["xhs"]
        unsupported = sorted(set(platforms) - SUPPORTED_PLATFORMS)
        if unsupported:
            errors.append(f"Unsupported platforms in dataset.json: {', '.join(unsupported)}")

        metadata = {
            "dataset_id": self._string_or_none(manifest.get("dataset_id")),
            "name": self._string_or_none(manifest.get("name")) or source_dir.name,
            "description": self._string_or_none(manifest.get("description")),
            "platforms": platforms,
            "keywords": self._as_string_list(manifest.get("keywords")),
        }

        return {
            "valid": not errors,
            "dataset_dir": str(source_dir),
            "dataset_json_path": str(dataset_json_path) if dataset_json_path.exists() else None,
            "raw_files": raw_files,
            "metadata": metadata,
            "warnings": warnings,
            "errors": errors,
        }

    def register_dataset(self, dataset_dir: str, import_mode: str = "copy") -> dict[str, Any]:
        import_mode = (import_mode or "copy").strip().lower()
        if import_mode not in {"copy", "link"}:
            raise McpAppError(
                ErrorCode.INVALID_ARGUMENT,
                "Unsupported dataset import mode",
                "import_mode must be 'copy' or 'link'",
            )

        validation = self.validate_dataset_bundle(dataset_dir)
        if not validation["valid"]:
            raise McpAppError(
                ErrorCode.INVALID_ARGUMENT,
                "Invalid dataset bundle",
                "; ".join(validation["errors"]),
                payload={"validation": validation},
            )

        source_dir = Path(validation["dataset_dir"]).resolve()
        source_manifest = self._read_manifest(source_dir / "dataset.json")
        dataset_id = self._string_or_none(source_manifest.get("dataset_id"))
        if dataset_id:
            dataset_id = self._normalize_dataset_id(dataset_id)
        else:
            dataset_id = make_dataset_id(validation["metadata"]["name"], self.config.datasets_dir)

        target_dir = source_dir
        if import_mode == "copy":
            self.storage.initialize()
            target_dir = (self.config.datasets_dir / dataset_id).resolve()
            if source_dir != target_dir:
                if target_dir.exists():
                    raise McpAppError(
                        ErrorCode.INVALID_ARGUMENT,
                        "Dataset already exists in MCP home",
                        f"dataset_id={dataset_id}, dataset_dir={target_dir}",
                    )
                shutil.copytree(source_dir, target_dir)
        else:
            self.storage.initialize()

        self._ensure_dataset_dirs(target_dir)
        dataset = self._dataset_from_manifest(dataset_id, target_dir, source_manifest, validation)
        payload = self._sync_manifest_payload(dataset, source_manifest)
        self._write_manifest(target_dir / "dataset.json", payload)
        self.storage.upsert_dataset(dataset)

        return {
            "dataset_id": dataset.dataset_id,
            "dataset_dir": dataset.dataset_dir,
            "dataset_json_path": str(target_dir / "dataset.json"),
            "import_mode": import_mode,
            "warnings": payload["warnings"],
        }

    def import_raw_files(
        self,
        dataset_id: str,
        platform: str,
        contents_path: str | None = None,
        comments_path: str | None = None,
        source_keyword: str | None = None,
    ) -> dict[str, Any]:
        dataset_id = (dataset_id or "").strip()
        platform = (platform or "").strip().lower()
        if not dataset_id:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Dataset ID is required")
        if platform not in RAW_FILE_NAMES:
            raise McpAppError(
                ErrorCode.UNSUPPORTED_PLATFORM,
                "Only xhs raw import is supported",
                f"platform={platform}",
            )
        if not contents_path and not comments_path:
            raise McpAppError(
                ErrorCode.INVALID_ARGUMENT,
                "At least one raw file path is required",
                "Provide contents_path or comments_path",
            )

        row = self._get_dataset_row(dataset_id)
        dataset_dir = Path(row["dataset_dir"])
        raw_dir = dataset_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        imported: dict[str, str] = {}
        for logical_name, source_path in (("contents", contents_path), ("comments", comments_path)):
            if not source_path:
                continue
            source = Path(source_path).expanduser()
            if not source.exists() or not source.is_file():
                raise McpAppError(
                    ErrorCode.INVALID_ARGUMENT,
                    "Raw file does not exist",
                    f"{logical_name}_path={source}",
                )
            destination = raw_dir / RAW_FILE_NAMES[platform][logical_name]
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
            imported[logical_name] = str(destination)

        if source_keyword:
            manifest = self._read_manifest(dataset_dir / "dataset.json")
            options = self._as_dict(manifest.get("options"))
            options["last_import_source_keyword"] = source_keyword
            manifest["options"] = options
            self._write_manifest(dataset_dir / "dataset.json", manifest)

        synced = self.sync_dataset_manifest(dataset_id)
        return {
            "dataset_id": dataset_id,
            "dataset_dir": str(dataset_dir),
            "raw_files": imported,
            "manifest": synced,
        }

    def sync_dataset_manifest(self, dataset_id: str) -> dict[str, Any]:
        dataset_id = (dataset_id or "").strip()
        if not dataset_id:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Dataset ID is required")

        row = self._get_dataset_row(dataset_id)
        dataset_dir = Path(row["dataset_dir"])
        self._ensure_dataset_dirs(dataset_dir)

        manifest_path = dataset_dir / "dataset.json"
        manifest = self._read_manifest(manifest_path)
        dataset = self._dataset_from_row(row, manifest)
        payload = self._sync_manifest_payload(dataset, manifest)
        self._write_manifest(manifest_path, payload)
        self.storage.upsert_dataset(dataset)

        return {
            "dataset_id": dataset.dataset_id,
            "dataset_dir": dataset.dataset_dir,
            "dataset_json_path": str(manifest_path),
            "updated": True,
            "files": payload["files"],
            "metrics": payload["metrics"],
            "warnings": payload["warnings"],
            "errors": payload["errors"],
        }

    def _get_dataset_row(self, dataset_id: str) -> dict[str, Any]:
        self.storage.initialize()
        row = self.storage.get_dataset_row(dataset_id)
        if row is None:
            raise McpAppError(ErrorCode.DATASET_NOT_FOUND, "Dataset not found", f"dataset_id={dataset_id}")
        return row

    def _dataset_from_manifest(
        self,
        dataset_id: str,
        dataset_dir: Path,
        manifest: dict[str, Any],
        validation: dict[str, Any],
    ) -> Dataset:
        now = utc_now_iso()
        metadata = validation["metadata"]
        detected = self._detect_files_and_metrics(dataset_dir)
        warnings = self._merge_messages(validation["warnings"], detected["warnings"])
        errors = self._merge_messages(validation["errors"], detected["errors"])
        return Dataset(
            dataset_id=dataset_id,
            name=self._string_or_none(manifest.get("name")) or metadata["name"],
            description=self._string_or_none(manifest.get("description")),
            status=self._string_or_none(manifest.get("status")) or "registered",
            platforms=self._as_string_list(manifest.get("platforms")) or metadata["platforms"],
            keywords=self._as_string_list(manifest.get("keywords")),
            options=self._as_dict(manifest.get("options")),
            dataset_dir=str(dataset_dir),
            created_at=self._string_or_none(manifest.get("created_at")) or now,
            updated_at=now,
            files=detected["files"],
            metrics=detected["metrics"],
            warnings=warnings,
            errors=errors,
        )

    def _dataset_from_row(self, row: dict[str, Any], manifest: dict[str, Any]) -> Dataset:
        detected = self._detect_files_and_metrics(Path(row["dataset_dir"]))
        warnings = self._merge_messages(self._as_string_list(manifest.get("warnings")), detected["warnings"])
        errors = self._merge_messages(self._as_string_list(manifest.get("errors")), detected["errors"])
        return Dataset(
            dataset_id=row["dataset_id"],
            name=self._string_or_none(manifest.get("name")) or row["name"],
            description=self._string_or_none(manifest.get("description")) or row["description"],
            status=self._string_or_none(manifest.get("status")) or row["status"],
            platforms=self._as_string_list(manifest.get("platforms")) or json.loads(row["platforms_json"]),
            keywords=self._as_string_list(manifest.get("keywords")) or json.loads(row["keywords_json"]),
            options=self._as_dict(manifest.get("options")) or json.loads(row["options_json"]),
            dataset_dir=row["dataset_dir"],
            created_at=self._string_or_none(manifest.get("created_at")) or row["created_at"],
            updated_at=utc_now_iso(),
            files=detected["files"],
            metrics=detected["metrics"],
            warnings=warnings,
            errors=errors,
        )

    def _sync_manifest_payload(self, dataset: Dataset, manifest: dict[str, Any]) -> dict[str, Any]:
        payload = {**manifest, **dataset.to_dict()}
        payload["files"] = dataset.files
        payload["metrics"] = dataset.metrics
        payload["warnings"] = dataset.warnings
        payload["errors"] = dataset.errors
        return payload

    def _detect_files_and_metrics(self, dataset_dir: Path) -> dict[str, Any]:
        files: dict[str, Any] = {"raw": {}, "reports": {}}
        metrics = {"content_count": 0, "comment_count": 0}
        warnings: list[str] = []
        errors: list[str] = []

        manifest = self._read_manifest(dataset_dir / "dataset.json")
        declared = self._as_string_list(manifest.get("platforms"))
        candidate_platforms = declared or sorted(RAW_FILE_NAMES.keys())
        for platform in candidate_platforms:
            names = RAW_FILE_NAMES.get(platform)
            if not names:
                continue
            for logical_name, file_name in names.items():
                path = dataset_dir / "raw" / file_name
                info = self._inspect_jsonl(path)
                if path.exists():
                    files["raw"][logical_name] = {
                        "platform": platform,
                        "path": str(path),
                        "line_count": info["line_count"],
                        "size_bytes": info["size_bytes"],
                    }
                    if logical_name == "contents":
                        metrics["content_count"] = info["line_count"]
                    elif logical_name == "comments":
                        metrics["comment_count"] = info["line_count"]
                errors.extend(info["errors"])
            if declared:
                break

        if "contents" not in files["raw"] and "comments" not in files["raw"]:
            warnings.append("No raw JSONL files found under raw/")
        elif "comments" not in files["raw"]:
            missing = [names["comments"] for names in RAW_FILE_NAMES.values()]
            warnings.append(f"raw comments file is missing (one of {missing}); comment analysis will be empty")
        return {"files": files, "metrics": metrics, "warnings": warnings, "errors": errors}

    @staticmethod
    def _inspect_jsonl(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"line_count": 0, "size_bytes": 0, "errors": []}

        errors = []
        line_count = 0
        try:
            size_bytes = path.stat().st_size
            with path.open("r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as exc:
                        errors.append(f"{path}:{line_no}: invalid JSONL row: {exc}")
                        continue
                    if not isinstance(value, dict):
                        errors.append(f"{path}:{line_no}: JSONL rows must be objects")
                        continue
                    line_count += 1
        except OSError as exc:
            errors.append(f"Cannot read {path}: {exc}")
            size_bytes = 0
        return {"line_count": line_count, "size_bytes": size_bytes, "errors": errors}

    @staticmethod
    def _ensure_dataset_dirs(dataset_dir: Path) -> None:
        dataset_dir.mkdir(parents=True, exist_ok=True)
        for child in ("raw", "media", "logs", "reports"):
            (dataset_dir / child).mkdir(exist_ok=True)

    @staticmethod
    def _read_manifest(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _normalize_dataset_id(value: str) -> str:
        normalized = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in value.strip())
        normalized = normalized.strip("._-")
        if not normalized:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Invalid dataset_id in dataset.json")
        return normalized

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _as_string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _merge_messages(*message_lists: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for messages in message_lists:
            for message in messages:
                if message not in seen:
                    seen.add(message)
                    merged.append(message)
        return merged
