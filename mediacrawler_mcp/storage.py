from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.models import Dataset


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
