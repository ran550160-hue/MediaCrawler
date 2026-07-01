from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.storage import Storage


VALID_TARGETS = {"contents", "comments"}
VALID_SORTS = {
    "contents": {"engagement_count", "like_count", "comment_count", "publish_time"},
    "comments": {"like_count", "publish_time"},
}


class QueryEngine:
    def __init__(self, storage: Storage):
        self.storage = storage

    def query_dataset(
        self,
        dataset_id: str,
        query: str,
        target: str = "comments",
        platform: str | None = None,
        source_keyword: str | None = None,
        limit: int = 20,
        sort_by: str | None = None,
    ) -> list[dict[str, Any]]:
        dataset_id = (dataset_id or "").strip()
        query = (query or "").strip()
        target = (target or "comments").strip().lower()
        platform = platform.strip().lower() if platform else None
        source_keyword = source_keyword.strip() if source_keyword else None
        limit = max(1, min(int(limit or 20), 100))

        if not dataset_id:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Dataset ID is required")
        if target not in VALID_TARGETS:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Invalid query target", f"target={target}")

        self.storage.initialize()
        row = self.storage.get_dataset_row(dataset_id)
        if row is None:
            raise McpAppError(ErrorCode.DATASET_NOT_FOUND, "Dataset not found", f"dataset_id={dataset_id}")

        duckdb_path = Path(row["dataset_dir"]) / "analysis.duckdb"
        if not duckdb_path.exists():
            raise McpAppError(
                ErrorCode.QUERY_FAILED,
                "Dataset is not normalized",
                "Call normalize_dataset before query_dataset",
            )

        sort_column = sort_by or ("like_count" if target == "comments" else "engagement_count")
        if sort_column not in VALID_SORTS[target]:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Invalid sort field", f"sort_by={sort_column}")

        sql, params = self._build_query(
            dataset_id=dataset_id,
            query=query,
            target=target,
            platform=platform,
            source_keyword=source_keyword,
            sort_by=sort_column,
            limit=limit,
        )
        with duckdb.connect(str(duckdb_path), read_only=True) as conn:
            rows = conn.execute(sql, params).fetchall()
            columns = [desc[0] for desc in conn.description]
        return [dict(zip(columns, row)) for row in rows]

    @staticmethod
    def _build_query(
        dataset_id: str,
        query: str,
        target: str,
        platform: str | None,
        source_keyword: str | None,
        sort_by: str,
        limit: int,
    ) -> tuple[str, list[Any]]:
        params: list[Any] = [dataset_id]
        where = ["dataset_id = ?"]
        if platform:
            where.append("platform = ?")
            params.append(platform)
        if source_keyword:
            where.append("source_keyword = ?")
            params.append(source_keyword)

        if target == "comments":
            text_expr = "comment_text"
            select = """
                platform, source_keyword, content_id, comment_id,
                comment_text AS text, like_count, publish_time,
                NULL AS url
            """
        else:
            text_expr = "concat_ws(' ', title, \"desc\", content_text)"
            select = """
                platform, source_keyword, content_id, NULL AS comment_id,
                concat_ws(' ', title, "desc", content_text) AS text,
                like_count, publish_time, url
            """

        for token in [part for part in query.split() if part.strip()]:
            where.append(f"{text_expr} LIKE ?")
            params.append(f"%{token}%")

        params.append(limit)
        sql = f"""
            SELECT {select}
            FROM {target}
            WHERE {" AND ".join(where)}
            ORDER BY {sort_by} DESC
            LIMIT ?
        """
        return sql, params
