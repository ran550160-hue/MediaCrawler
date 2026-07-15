from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.models import (
    Dataset,
    DiscoveryContext,
    EvidenceItem,
    EvidenceObservation,
    DuplicateCandidateGroup,
    RelevanceDecision,
    ResearchRun,
)


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS datasets (
      dataset_id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      description TEXT,
      status TEXT NOT NULL,
      platforms_json TEXT NOT NULL,
      keywords_json TEXT NOT NULL,
      options_json TEXT NOT NULL,
      dataset_dir TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      last_crawled_at TEXT,
      content_count INTEGER DEFAULT 0,
      comment_count INTEGER DEFAULT 0,
      warning_count INTEGER DEFAULT 0,
      error_count INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
      task_id TEXT PRIMARY KEY,
      dataset_id TEXT NOT NULL,
      task_type TEXT NOT NULL,
      status TEXT NOT NULL,
      progress REAL DEFAULT 0,
      platforms_json TEXT,
      keywords_json TEXT,
      options_json TEXT,
      pid INTEGER,
      log_path TEXT,
      error_code TEXT,
      error_message TEXT,
      created_at TEXT NOT NULL,
      started_at TEXT,
      finished_at TEXT,
      updated_at TEXT NOT NULL,
      FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS login_sessions (
      login_session_id TEXT PRIMARY KEY,
      platform TEXT NOT NULL,
      account_name TEXT,
      status TEXT NOT NULL,
      qr_image_path TEXT,
      profile_dir TEXT,
      expires_at TEXT,
      message TEXT,
      pid INTEGER,
      worker_log_path TEXT,
      error_code TEXT,
      error_message TEXT,
      verification_attempts INTEGER DEFAULT 0,
      last_verify_error_code TEXT,
      last_verify_message TEXT,
      observed_cookie_at TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reports (
      report_id TEXT PRIMARY KEY,
      dataset_id TEXT NOT NULL,
      report_type TEXT NOT NULL,
      status TEXT NOT NULL,
      report_md_path TEXT,
      report_html_path TEXT,
      summary_json_path TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS accounts (
      account_id TEXT PRIMARY KEY,
      platform TEXT NOT NULL,
      account_name TEXT,
      profile_dir TEXT NOT NULL,
      status TEXT NOT NULL,
      last_login_at TEXT,
      last_checked_at TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_runs (
      research_run_id TEXT PRIMARY KEY,
      question TEXT NOT NULL,
      platforms_json TEXT NOT NULL,
      keywords_json TEXT NOT NULL,
      time_range_json TEXT,
      sample_limits_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      status TEXT NOT NULL,
      collection_task_ids_json TEXT NOT NULL,
      dataset_version TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence_items (
      evidence_id TEXT PRIMARY KEY,
      research_run_id TEXT NOT NULL,
      platform TEXT NOT NULL,
      content_type TEXT NOT NULL,
      content_id TEXT NOT NULL,
      author_id TEXT NOT NULL,
      source_url TEXT NOT NULL,
      published_at TEXT,
      collected_at TEXT,
      query_keyword TEXT NOT NULL,
      title TEXT NOT NULL,
      full_text TEXT NOT NULL,
      raw_content_hash TEXT NOT NULL,
      canonical_content_hash TEXT NOT NULL,
      content_revision_id TEXT NOT NULL,
      hash_algorithm_version TEXT NOT NULL,
      engagement_json TEXT NOT NULL,
      raw_record_reference_json TEXT NOT NULL,
      metadata_json TEXT NOT NULL,
      FOREIGN KEY(research_run_id) REFERENCES research_runs(research_run_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_evidence_items_run
    ON evidence_items(research_run_id, platform, content_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence_item_versions (
      evidence_id TEXT PRIMARY KEY,
      research_run_id TEXT NOT NULL,
      source_entity_id TEXT NOT NULL,
      platform TEXT NOT NULL,
      content_type TEXT NOT NULL,
      content_id TEXT NOT NULL,
      author_id TEXT NOT NULL,
      source_url TEXT NOT NULL,
      published_at TEXT,
      collected_at TEXT,
      query_keyword TEXT NOT NULL,
      title TEXT NOT NULL,
      full_text TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      raw_record_hash TEXT NOT NULL,
      canonical_content_hash TEXT NOT NULL,
      engagement_json TEXT NOT NULL,
      raw_record_reference_json TEXT NOT NULL,
      metadata_json TEXT NOT NULL,
      schema_version TEXT NOT NULL,
      FOREIGN KEY(research_run_id) REFERENCES research_runs(research_run_id),
      UNIQUE(research_run_id, source_entity_id, content_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence_discovery_contexts (
      discovery_id TEXT PRIMARY KEY,
      evidence_id TEXT NOT NULL,
      keyword TEXT NOT NULL,
      collection_task_id TEXT NOT NULL,
      discovered_at TEXT,
      result_position INTEGER,
      FOREIGN KEY(evidence_id) REFERENCES evidence_item_versions(evidence_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence_observations (
      observation_id TEXT PRIMARY KEY,
      evidence_id TEXT NOT NULL,
      raw_record_hash TEXT NOT NULL,
      observed_at TEXT,
      engagement_json TEXT NOT NULL,
      raw_record_reference_json TEXT NOT NULL,
      metadata_json TEXT NOT NULL,
      FOREIGN KEY(evidence_id) REFERENCES evidence_item_versions(evidence_id),
      UNIQUE(evidence_id, raw_record_hash)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_evidence_versions_run
    ON evidence_item_versions(research_run_id, platform, content_type, content_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_evidence_discoveries_evidence
    ON evidence_discovery_contexts(evidence_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence_analysis_runs (
      analysis_run_id TEXT PRIMARY KEY,
      research_run_id TEXT NOT NULL,
      relevance_algorithm_version TEXT NOT NULL,
      duplicate_algorithm_version TEXT NOT NULL,
      configuration_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(research_run_id) REFERENCES research_runs(research_run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS relevance_decisions (
      analysis_run_id TEXT NOT NULL,
      evidence_id TEXT NOT NULL,
      decision TEXT NOT NULL,
      score REAL NOT NULL,
      reasons_json TEXT NOT NULL,
      algorithm_version TEXT NOT NULL,
      PRIMARY KEY(analysis_run_id, evidence_id),
      FOREIGN KEY(analysis_run_id) REFERENCES evidence_analysis_runs(analysis_run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_candidate_groups (
      duplicate_group_id TEXT PRIMARY KEY,
      analysis_run_id TEXT NOT NULL,
      group_type TEXT NOT NULL,
      algorithm_version TEXT NOT NULL,
      rationale_json TEXT NOT NULL,
      FOREIGN KEY(analysis_run_id) REFERENCES evidence_analysis_runs(analysis_run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_candidate_members (
      duplicate_group_id TEXT NOT NULL,
      evidence_id TEXT NOT NULL,
      PRIMARY KEY(duplicate_group_id, evidence_id),
      FOREIGN KEY(duplicate_group_id) REFERENCES duplicate_candidate_groups(duplicate_group_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS duplicate_candidate_edges (
      analysis_run_id TEXT NOT NULL,
      left_evidence_id TEXT NOT NULL,
      right_evidence_id TEXT NOT NULL,
      edge_type TEXT NOT NULL,
      similarity REAL NOT NULL,
      algorithm_version TEXT NOT NULL,
      threshold REAL,
      PRIMARY KEY(analysis_run_id, left_evidence_id, right_evidence_id, edge_type)
    )
    """,
)


class Storage:
    def __init__(self, config: McpConfig):
        self.config = config

    @property
    def db_path(self) -> Path:
        return self.config.metadata_db_path

    def connect(self) -> sqlite3.Connection:
        self.config.home.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        self.config.home.mkdir(parents=True, exist_ok=True)
        self.config.datasets_dir.mkdir(parents=True, exist_ok=True)
        self.config.logs_dir.mkdir(parents=True, exist_ok=True)
        self.config.accounts_dir.mkdir(parents=True, exist_ok=True)
        self.config.login_qrcodes_dir.mkdir(parents=True, exist_ok=True)
        self.config.locks_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            for statement in SCHEMA_STATEMENTS:
                conn.execute(statement)
            self._apply_migrations(conn)
            conn.commit()

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        self._ensure_columns(
            conn,
            "login_sessions",
            {
                "pid": "INTEGER",
                "worker_log_path": "TEXT",
                "error_code": "TEXT",
                "error_message": "TEXT",
                "verification_attempts": "INTEGER DEFAULT 0",
                "last_verify_error_code": "TEXT",
                "last_verify_message": "TEXT",
                "observed_cookie_at": "TEXT",
            },
        )
        self._ensure_columns(conn, "evidence_analysis_runs", {
            "evidence_set_fingerprint": "TEXT",
            "status": "TEXT",
        })
        self._ensure_columns(
            conn,
            "research_runs",
            {
                "source_dataset_id": "TEXT",
                "normalization_version": "TEXT",
                "ledger_schema_version": "TEXT",
                "build_started_at": "TEXT",
                "build_completed_at": "TEXT",
                "build_error": "TEXT",
                "hash_algorithm_version": "TEXT",
            },
        )
        self._ensure_columns(
            conn,
            "evidence_item_versions",
            {
                "content_revision_id": "TEXT",
                "hash_algorithm_version": "TEXT",
            },
        )

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection, table_name: str, columns: dict[str, str]) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
        for column_name, column_type in columns.items():
            if column_name not in existing:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    def upsert_dataset(self, dataset: Dataset) -> None:
        payload = dataset.to_dict()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO datasets (
                    dataset_id, name, description, status, platforms_json,
                    keywords_json, options_json, dataset_dir, created_at,
                    updated_at, content_count, comment_count, warning_count,
                    error_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset_id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    status=excluded.status,
                    platforms_json=excluded.platforms_json,
                    keywords_json=excluded.keywords_json,
                    options_json=excluded.options_json,
                    dataset_dir=excluded.dataset_dir,
                    updated_at=excluded.updated_at,
                    content_count=excluded.content_count,
                    comment_count=excluded.comment_count,
                    warning_count=excluded.warning_count,
                    error_count=excluded.error_count
                """,
                (
                    dataset.dataset_id,
                    dataset.name,
                    dataset.description,
                    dataset.status,
                    json.dumps(dataset.platforms, ensure_ascii=False),
                    json.dumps(dataset.keywords, ensure_ascii=False),
                    json.dumps(dataset.options, ensure_ascii=False),
                    dataset.dataset_dir,
                    dataset.created_at,
                    dataset.updated_at,
                    dataset.metrics.get("content_count", 0),
                    dataset.metrics.get("comment_count", 0),
                    len(payload.get("warnings", [])),
                    len(payload.get("errors", [])),
                ),
            )
            conn.commit()

    def get_dataset_row(self, dataset_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,)).fetchone()
        return dict(row) if row else None

    def list_dataset_rows(
        self,
        keyword: str | None = None,
        platform: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []

        if keyword:
            where.append("(name LIKE ? OR keywords_json LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like])
        if platform:
            where.append("platforms_json LIKE ?")
            params.append(f"%{platform}%")
        if status:
            where.append("status = ?")
            params.append(status)

        sql = "SELECT * FROM datasets"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, dataset_id DESC LIMIT ?"
        params.append(max(1, min(limit, 100)))

        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def find_dataset_row_by_collection_task_id(
        self,
        collection_task_id: str,
        platform: str | None = None,
    ) -> dict[str, Any] | None:
        collection_task_id = (collection_task_id or "").strip()
        if not collection_task_id:
            return None

        where = ["options_json LIKE ?"]
        params: list[Any] = [f"%{collection_task_id}%"]
        if platform:
            where.append("platforms_json LIKE ?")
            params.append(f"%{platform.strip().lower()}%")

        sql = (
            "SELECT * FROM datasets WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at ASC, dataset_id ASC"
        )
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        for row in rows:
            payload = dict(row)
            try:
                options = json.loads(payload.get("options_json") or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(options, dict):
                continue
            if str(options.get("collection_task_id") or options.get("task_id") or "").strip() == collection_task_id:
                return payload
        return None

    def upsert_report(
        self,
        report_id: str,
        dataset_id: str,
        report_type: str,
        status: str,
        report_md_path: str,
        report_html_path: str,
        summary_json_path: str,
        created_at: str,
        updated_at: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO reports (
                    report_id, dataset_id, report_type, status, report_md_path,
                    report_html_path, summary_json_path, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_id) DO UPDATE SET
                    status=excluded.status,
                    report_md_path=excluded.report_md_path,
                    report_html_path=excluded.report_html_path,
                    summary_json_path=excluded.summary_json_path,
                    updated_at=excluded.updated_at
                """,
                (
                    report_id,
                    dataset_id,
                    report_type,
                    status,
                    report_md_path,
                    report_html_path,
                    summary_json_path,
                    created_at,
                    updated_at,
                ),
            )
            conn.commit()

    def get_report_row(self, dataset_id: str, report_id: str | None = None) -> dict[str, Any] | None:
        with self.connect() as conn:
            if report_id:
                row = conn.execute(
                    "SELECT * FROM reports WHERE dataset_id = ? AND report_id = ?",
                    (dataset_id, report_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM reports
                    WHERE dataset_id = ?
                    ORDER BY created_at DESC, report_id DESC
                    LIMIT 1
                    """,
                    (dataset_id,),
                ).fetchone()
        return dict(row) if row else None

    def create_task(
        self,
        task_id: str,
        dataset_id: str,
        task_type: str,
        status: str,
        platforms: list[str],
        keywords: list[str],
        options: dict[str, Any],
        log_path: str,
        created_at: str,
        updated_at: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, dataset_id, task_type, status, progress,
                    platforms_json, keywords_json, options_json, log_path,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    dataset_id,
                    task_type,
                    status,
                    0,
                    json.dumps(platforms, ensure_ascii=False),
                    json.dumps(keywords, ensure_ascii=False),
                    json.dumps(options, ensure_ascii=False),
                    log_path,
                    created_at,
                    updated_at,
                ),
            )
            conn.commit()

    def update_task(
        self,
        task_id: str,
        status: str | None = None,
        progress: float | None = None,
        pid: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        updated_at: str | None = None,
    ) -> None:
        updates = []
        params: list[Any] = []
        for column, value in (
            ("status", status),
            ("progress", progress),
            ("pid", pid),
            ("error_code", error_code),
            ("error_message", error_message),
            ("started_at", started_at),
            ("finished_at", finished_at),
            ("updated_at", updated_at),
        ):
            if value is not None:
                updates.append(f"{column} = ?")
                params.append(value)
        if not updates:
            return
        params.append(task_id)
        with self.connect() as conn:
            conn.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ?", params)
            conn.commit()

    def get_task_row(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def create_research_run(self, research_run: ResearchRun) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO research_runs (
                    research_run_id, question, platforms_json, keywords_json,
                    time_range_json, sample_limits_json, created_at, status,
                    collection_task_ids_json, dataset_version, source_dataset_id,
                    normalization_version, ledger_schema_version, build_started_at,
                    build_completed_at, build_error, hash_algorithm_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    research_run.research_run_id,
                    research_run.question,
                    json.dumps(research_run.platforms, ensure_ascii=False),
                    json.dumps(research_run.keywords, ensure_ascii=False),
                    json.dumps(research_run.time_range, ensure_ascii=False) if research_run.time_range is not None else None,
                    json.dumps(research_run.sample_limits, ensure_ascii=False),
                    research_run.created_at,
                    research_run.status,
                    json.dumps(research_run.collection_task_ids, ensure_ascii=False),
                    research_run.dataset_version,
                    research_run.source_dataset_id,
                    research_run.normalization_version,
                    research_run.ledger_schema_version,
                    research_run.build_started_at,
                    research_run.build_completed_at,
                    research_run.build_error,
                    research_run.hash_algorithm_version,
                ),
            )
            conn.commit()

    def get_research_run_row(self, research_run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_runs WHERE research_run_id = ?", (research_run_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_research_run(
        self,
        research_run_id: str,
        *,
        status: str | None = None,
        dataset_version: str | None = None,
        source_dataset_id: str | None = None,
        normalization_version: str | None = None,
        ledger_schema_version: str | None = None,
        build_started_at: str | None = None,
        build_completed_at: str | None = None,
        build_error: str | None = None,
    ) -> None:
        updates: list[str] = []
        params: list[Any] = []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if dataset_version is not None:
            updates.append("dataset_version = ?")
            params.append(dataset_version)
        for column, value in (
            ("source_dataset_id", source_dataset_id),
            ("normalization_version", normalization_version),
            ("ledger_schema_version", ledger_schema_version),
            ("build_started_at", build_started_at),
            ("build_completed_at", build_completed_at),
            ("build_error", build_error),
        ):
            if value is not None:
                updates.append(f"{column} = ?")
                params.append(value)
        if not updates:
            return
        params.append(research_run_id)
        with self.connect() as conn:
            conn.execute(f"UPDATE research_runs SET {', '.join(updates)} WHERE research_run_id = ?", params)
            conn.commit()

    def upsert_evidence_item(self, evidence: EvidenceItem) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO evidence_items (
                    evidence_id, research_run_id, platform, content_type,
                    content_id, author_id, source_url, published_at,
                    collected_at, query_keyword, title, full_text,
                    raw_content_hash, canonical_content_hash, engagement_json,
                    raw_record_reference_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    author_id=excluded.author_id,
                    source_url=excluded.source_url,
                    published_at=excluded.published_at,
                    collected_at=excluded.collected_at,
                    query_keyword=excluded.query_keyword,
                    title=excluded.title,
                    full_text=excluded.full_text,
                    raw_content_hash=excluded.raw_content_hash,
                    canonical_content_hash=excluded.canonical_content_hash,
                    engagement_json=excluded.engagement_json,
                    raw_record_reference_json=excluded.raw_record_reference_json,
                    metadata_json=excluded.metadata_json
                """,
                (
                    evidence.evidence_id,
                    evidence.research_run_id,
                    evidence.platform,
                    evidence.content_type,
                    evidence.content_id,
                    evidence.author_id,
                    evidence.source_url,
                    evidence.published_at,
                    evidence.collected_at,
                    evidence.query_keyword,
                    evidence.title,
                    evidence.full_text,
                    evidence.raw_content_hash,
                    evidence.canonical_content_hash,
                    json.dumps(evidence.engagement, ensure_ascii=False, sort_keys=True),
                    json.dumps(evidence.raw_record_reference, ensure_ascii=False, sort_keys=True),
                    json.dumps(evidence.metadata, ensure_ascii=False, sort_keys=True),
                ),
            )
            conn.commit()

    def list_evidence_item_rows(self, research_run_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM evidence_items
                WHERE research_run_id = ?
                ORDER BY platform, content_type, content_id, evidence_id
                LIMIT ?
                """,
                (research_run_id, max(1, min(limit, 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_evidence_build_started(
        self,
        research_run_id: str,
        *,
        source_dataset_id: str,
        dataset_version: str,
        normalization_version: str,
        ledger_schema_version: str,
        hash_algorithm_version: str,
        started_at: str,
    ) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE research_runs
                SET status = 'building', source_dataset_id = ?, dataset_version = ?,
                    normalization_version = ?, ledger_schema_version = ?,
                    hash_algorithm_version = ?, build_started_at = ?,
                    build_completed_at = NULL, build_error = NULL
                WHERE research_run_id = ? AND status IN ('created', 'build_failed')
                """,
                (
                    source_dataset_id,
                    dataset_version,
                    normalization_version,
                    ledger_schema_version,
                    hash_algorithm_version,
                    started_at,
                    research_run_id,
                ),
            )
            conn.commit()
        return cursor.rowcount == 1

    def mark_evidence_build_failed(self, research_run_id: str, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE research_runs SET status = 'build_failed', build_error = ? WHERE research_run_id = ?",
                (error[:1000], research_run_id),
            )
            conn.commit()

    def recover_stale_evidence_build(self, research_run_id: str, error: str) -> bool:
        """Release a build lease left by a terminated process; never completes it."""
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE research_runs
                SET status = 'build_failed', build_error = ?
                WHERE research_run_id = ? AND status = 'building'
                """,
                (error[:1000], research_run_id),
            )
            conn.commit()
        return cursor.rowcount == 1

    def complete_evidence_build(
        self,
        research_run_id: str,
        evidence_items: list[EvidenceItem],
        discovery_contexts: list[DiscoveryContext],
        observations: list[EvidenceObservation],
        completed_at: str,
    ) -> None:
        """Atomically persist an immutable ledger and mark its run completed."""
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO evidence_item_versions (
                    evidence_id, research_run_id, source_entity_id, platform,
                    content_type, content_id, author_id, source_url, published_at,
                    collected_at, query_keyword, title, full_text, content_hash,
                    raw_record_hash, canonical_content_hash, content_revision_id,
                    hash_algorithm_version, engagement_json, raw_record_reference_json,
                    metadata_json, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO NOTHING
                """,
                [
                    (
                        item.evidence_id,
                        item.research_run_id,
                        item.source_entity_id,
                        item.platform,
                        item.content_type,
                        item.content_id,
                        item.author_id,
                        item.source_url,
                        item.published_at,
                        item.collected_at,
                        item.query_keyword,
                        item.title,
                        item.full_text,
                        item.content_hash,
                        item.raw_record_hash,
                        item.canonical_content_hash,
                        item.content_revision_id,
                        item.hash_algorithm_version,
                        json.dumps(item.engagement, ensure_ascii=False, sort_keys=True),
                        json.dumps(item.raw_record_reference, ensure_ascii=False, sort_keys=True),
                        json.dumps(item.metadata, ensure_ascii=False, sort_keys=True),
                        "evidence-ledger/v2",
                    )
                    for item in evidence_items
                ],
            )
            conn.executemany(
                """
                INSERT INTO evidence_discovery_contexts (
                    discovery_id, evidence_id, keyword, collection_task_id,
                    discovered_at, result_position
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(discovery_id) DO NOTHING
                """,
                [
                    (
                        context.discovery_id,
                        context.evidence_id,
                        context.keyword,
                        context.collection_task_id,
                        context.discovered_at,
                        context.result_position,
                    )
                    for context in discovery_contexts
                ],
            )
            conn.executemany(
                """
                INSERT INTO evidence_observations (
                    observation_id, evidence_id, raw_record_hash, observed_at,
                    engagement_json, raw_record_reference_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_id) DO NOTHING
                """,
                [
                    (
                        observation.observation_id,
                        observation.evidence_id,
                        observation.raw_record_hash,
                        observation.observed_at,
                        json.dumps(observation.engagement, ensure_ascii=False, sort_keys=True),
                        json.dumps(observation.raw_record_reference, ensure_ascii=False, sort_keys=True),
                        json.dumps(observation.metadata, ensure_ascii=False, sort_keys=True),
                    )
                    for observation in observations
                ],
            )
            conn.execute(
                """
                UPDATE research_runs
                SET status = 'completed', build_completed_at = ?, build_error = NULL
                WHERE research_run_id = ?
                """,
                (completed_at, research_run_id),
            )

    def get_evidence_version_row(self, research_run_id: str, evidence_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM evidence_item_versions
                WHERE research_run_id = ? AND evidence_id = ?
                """,
                (research_run_id, evidence_id),
            ).fetchone()
        return dict(row) if row else None

    def list_evidence_version_rows(
        self,
        research_run_id: str,
        *,
        platform: str | None,
        content_type: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        where = ["research_run_id = ?"]
        params: list[Any] = [research_run_id]
        if platform:
            where.append("platform = ?")
            params.append(platform)
        if content_type:
            where.append("content_type = ?")
            params.append(content_type)
        clause = " AND ".join(where)
        with self.connect() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM evidence_item_versions WHERE {clause}", params).fetchone()[0])
            rows = conn.execute(
                f"""
                SELECT * FROM evidence_item_versions WHERE {clause}
                ORDER BY platform, content_type, content_id, evidence_id
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
        return [dict(row) for row in rows], total

    def list_evidence_version_rows_by_source_entity(
        self, source_entity_id: str, limit: int
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM evidence_item_versions
                WHERE source_entity_id = ?
                ORDER BY published_at, evidence_id
                LIMIT ?
                """,
                (source_entity_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_discovery_context_rows(self, evidence_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM evidence_discovery_contexts WHERE evidence_id = ?
                ORDER BY keyword, collection_task_id, discovered_at, result_position, discovery_id
                """,
                (evidence_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_evidence_observation_rows(self, evidence_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM evidence_observations WHERE evidence_id = ?
                ORDER BY observed_at, observation_id
                """,
                (evidence_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_evidence_analysis(
        self,
        analysis_run_id: str,
        research_run_id: str,
        relevance_algorithm_version: str,
        duplicate_algorithm_version: str,
        configuration: dict[str, Any],
        created_at: str,
        decisions: list[RelevanceDecision],
        groups: list[DuplicateCandidateGroup],
        edges: list[dict[str, Any]],
        evidence_set_fingerprint: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO evidence_analysis_runs (
                    analysis_run_id, research_run_id, relevance_algorithm_version,
                    duplicate_algorithm_version, configuration_json, created_at,
                    evidence_set_fingerprint, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (analysis_run_id, research_run_id, relevance_algorithm_version, duplicate_algorithm_version, json.dumps(configuration, ensure_ascii=False, sort_keys=True), created_at, evidence_set_fingerprint, "completed"),
            )
            conn.executemany(
                """
                INSERT INTO relevance_decisions (
                    analysis_run_id, evidence_id, decision, score, reasons_json, algorithm_version
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(item.analysis_run_id, item.evidence_id, item.decision, item.score, json.dumps(item.reasons, ensure_ascii=False, sort_keys=True), item.algorithm_version) for item in decisions],
            )
            conn.executemany(
                """
                INSERT INTO duplicate_candidate_groups (
                    duplicate_group_id, analysis_run_id, group_type, algorithm_version, rationale_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [(group.duplicate_group_id, group.analysis_run_id, group.group_type, group.algorithm_version, json.dumps(group.rationale, ensure_ascii=False, sort_keys=True)) for group in groups],
            )
            conn.executemany(
                "INSERT INTO duplicate_candidate_members (duplicate_group_id, evidence_id) VALUES (?, ?)",
                [(group.duplicate_group_id, evidence_id) for group in groups for evidence_id in group.evidence_ids],
            )
            conn.executemany(
                """INSERT INTO duplicate_candidate_edges (
                    analysis_run_id, left_evidence_id, right_evidence_id, edge_type,
                    similarity, algorithm_version, threshold
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [(analysis_run_id, edge["left_evidence_id"], edge["right_evidence_id"], edge["edge_type"], edge["similarity"], edge["algorithm_version"], edge.get("threshold")) for edge in edges],
            )

    def list_duplicate_edge_rows(self, analysis_run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM duplicate_candidate_edges WHERE analysis_run_id = ? ORDER BY edge_type, left_evidence_id, right_evidence_id", (analysis_run_id,)).fetchall()
        return [dict(row) for row in rows]

    def get_evidence_analysis_row(self, analysis_run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM evidence_analysis_runs WHERE analysis_run_id = ?", (analysis_run_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_relevance_decision_rows(self, analysis_run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM relevance_decisions WHERE analysis_run_id = ? ORDER BY evidence_id", (analysis_run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def list_duplicate_group_rows(self, analysis_run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            groups = conn.execute(
                "SELECT * FROM duplicate_candidate_groups WHERE analysis_run_id = ? ORDER BY group_type, duplicate_group_id", (analysis_run_id,)
            ).fetchall()
            result = []
            for group in groups:
                payload = dict(group)
                payload["evidence_ids"] = [row[0] for row in conn.execute(
                    "SELECT evidence_id FROM duplicate_candidate_members WHERE duplicate_group_id = ? ORDER BY evidence_id", (payload["duplicate_group_id"],)
                ).fetchall()]
                result.append(payload)
        return result

    def upsert_account(
        self,
        account_id: str,
        platform: str,
        account_name: str,
        profile_dir: str,
        status: str,
        last_login_at: str | None,
        last_checked_at: str | None,
        created_at: str,
        updated_at: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                    account_id, platform, account_name, profile_dir, status,
                    last_login_at, last_checked_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    platform=excluded.platform,
                    account_name=excluded.account_name,
                    profile_dir=excluded.profile_dir,
                    status=excluded.status,
                    last_login_at=excluded.last_login_at,
                    last_checked_at=excluded.last_checked_at,
                    updated_at=excluded.updated_at
                """,
                (
                    account_id,
                    platform,
                    account_name,
                    profile_dir,
                    status,
                    last_login_at,
                    last_checked_at,
                    created_at,
                    updated_at,
                ),
            )
            conn.commit()

    def get_account_row(self, account_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
        return dict(row) if row else None

    def upsert_login_session(
        self,
        login_session_id: str,
        platform: str,
        account_name: str,
        status: str,
        qr_image_path: str | None,
        profile_dir: str | None,
        expires_at: str | None,
        message: str | None,
        created_at: str,
        updated_at: str,
        pid: int | None = None,
        worker_log_path: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        verification_attempts: int | None = None,
        last_verify_error_code: str | None = None,
        last_verify_message: str | None = None,
        observed_cookie_at: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO login_sessions (
                    login_session_id, platform, account_name, status,
                    qr_image_path, profile_dir, expires_at, message,
                    pid, worker_log_path, error_code, error_message,
                    verification_attempts, last_verify_error_code,
                    last_verify_message, observed_cookie_at,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(login_session_id) DO UPDATE SET
                    platform=excluded.platform,
                    account_name=excluded.account_name,
                    status=excluded.status,
                    qr_image_path=excluded.qr_image_path,
                    profile_dir=excluded.profile_dir,
                    expires_at=excluded.expires_at,
                    message=excluded.message,
                    pid=COALESCE(excluded.pid, login_sessions.pid),
                    worker_log_path=COALESCE(excluded.worker_log_path, login_sessions.worker_log_path),
                    error_code=excluded.error_code,
                    error_message=excluded.error_message,
                    verification_attempts=COALESCE(excluded.verification_attempts, login_sessions.verification_attempts),
                    last_verify_error_code=COALESCE(excluded.last_verify_error_code, login_sessions.last_verify_error_code),
                    last_verify_message=COALESCE(excluded.last_verify_message, login_sessions.last_verify_message),
                    observed_cookie_at=COALESCE(excluded.observed_cookie_at, login_sessions.observed_cookie_at),
                    updated_at=excluded.updated_at
                """,
                (
                    login_session_id,
                    platform,
                    account_name,
                    status,
                    qr_image_path,
                    profile_dir,
                    expires_at,
                    message,
                    pid,
                    worker_log_path,
                    error_code,
                    error_message,
                    verification_attempts,
                    last_verify_error_code,
                    last_verify_message,
                    observed_cookie_at,
                    created_at,
                    updated_at,
                ),
            )
            conn.commit()

    def get_login_session_row(self, login_session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM login_sessions WHERE login_session_id = ?",
                (login_session_id,),
            ).fetchone()
        return dict(row) if row else None
