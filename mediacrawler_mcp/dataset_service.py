from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.models import Dataset
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.utils import make_dataset_id, utc_now_iso


SUPPORTED_PLATFORMS = {"xhs", "douyin"}


class DatasetService:
    def __init__(self, config: McpConfig, storage: Storage):
        self.config = config
        self.storage = storage

    def create_dataset(
        self,
        name: str,
        platforms: list[str],
        keywords: list[str],
        description: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> Dataset:
        name = (name or "").strip()
        platforms = [platform.strip().lower() for platform in platforms or [] if platform and platform.strip()]
        keywords = [keyword.strip() for keyword in keywords or [] if keyword and keyword.strip()]

        if not name:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Dataset name is required")
        if not platforms:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "At least one platform is required")
        if not keywords:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "At least one keyword is required")

        unsupported = sorted(set(platforms) - SUPPORTED_PLATFORMS)
        if unsupported:
            raise McpAppError(
                ErrorCode.UNSUPPORTED_PLATFORM,
                "Only xhs is supported in the first MCP phase",
                f"Unsupported platforms: {', '.join(unsupported)}",
            )
        if platforms != ["xhs"]:
            raise McpAppError(
                ErrorCode.UNSUPPORTED_PLATFORM,
                "Only platforms=['xhs'] is supported in the first MCP phase",
                f"Received platforms: {platforms}",
            )

        self.storage.initialize()
        dataset_id = make_dataset_id(name, self.config.datasets_dir)
        dataset_dir = self.config.datasets_dir / dataset_id
        self._create_dataset_dirs(dataset_dir)

        now = utc_now_iso()
        dataset = Dataset(
            dataset_id=dataset_id,
            name=name,
            description=description,
            status="created",
            platforms=platforms,
            keywords=keywords,
            options=options or {},
            dataset_dir=str(dataset_dir),
            created_at=now,
            updated_at=now,
        )

        self._write_dataset_json(dataset_dir / "dataset.json", dataset)
        self.storage.upsert_dataset(dataset)
        return dataset

    def list_datasets(
        self,
        keyword: str | None = None,
        platform: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.storage.initialize()
        keyword = keyword.strip() if keyword else None
        platform = platform.strip().lower() if platform else None
        status = status.strip() if status else None

        rows = self.storage.list_dataset_rows(
            keyword=keyword,
            platform=platform,
            status=status,
            limit=limit,
        )
        return [self._row_to_summary(row) for row in rows]

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        dataset_id = (dataset_id or "").strip()
        if not dataset_id:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Dataset ID is required")

        self.storage.initialize()
        row = self.storage.get_dataset_row(dataset_id)
        if row is None:
            raise McpAppError(
                ErrorCode.DATASET_NOT_FOUND,
                "Dataset not found",
                f"dataset_id={dataset_id}",
            )

        dataset_json_path = Path(row["dataset_dir"]) / "dataset.json"
        dataset_payload: dict[str, Any] = {}
        if dataset_json_path.exists():
            dataset_payload = json.loads(dataset_json_path.read_text(encoding="utf-8"))

        return {
            **dataset_payload,
            "sqlite": self._row_to_summary(row),
            "dataset_json_path": str(dataset_json_path),
        }

    @staticmethod
    def _create_dataset_dirs(dataset_dir: Path) -> None:
        dataset_dir.mkdir(parents=True, exist_ok=False)
        for child in ("raw", "logs", "reports"):
            (dataset_dir / child).mkdir()

    @staticmethod
    def _write_dataset_json(path: Path, dataset: Dataset) -> None:
        path.write_text(
            json.dumps(dataset.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _row_to_summary(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "dataset_id": row["dataset_id"],
            "name": row["name"],
            "description": row["description"],
            "status": row["status"],
            "platforms": json.loads(row["platforms_json"]),
            "keywords": json.loads(row["keywords_json"]),
            "options": json.loads(row["options_json"]),
            "dataset_dir": row["dataset_dir"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "content_count": row["content_count"],
            "comment_count": row["comment_count"],
        }
