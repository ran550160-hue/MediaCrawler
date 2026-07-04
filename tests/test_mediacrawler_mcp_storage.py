import sqlite3

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.storage import Storage


def _config(tmp_path):
    return McpConfig(
        home=tmp_path,
        browser_mode="persistent_context",
        cdp_endpoint=None,
        max_concurrent_tasks=1,
        default_timeout_seconds=300,
    )


def test_storage_initialize_creates_database_and_tables(tmp_path):
    storage = Storage(_config(tmp_path))

    storage.initialize()

    assert (tmp_path / "metadata.sqlite").exists()
    assert (tmp_path / "datasets").is_dir()
    assert (tmp_path / "logs").is_dir()

    with sqlite3.connect(tmp_path / "metadata.sqlite") as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert {"datasets", "tasks", "login_sessions", "reports", "accounts"} <= tables
    assert journal_mode.lower() == "wal"

    with sqlite3.connect(tmp_path / "metadata.sqlite") as conn:
        login_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(login_sessions)").fetchall()
        }
    assert {"pid", "worker_log_path", "error_code", "error_message"} <= login_columns


def test_storage_initialize_is_idempotent(tmp_path):
    storage = Storage(_config(tmp_path))

    storage.initialize()
    storage.initialize()

    with sqlite3.connect(tmp_path / "metadata.sqlite") as conn:
        table_count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0]

    assert table_count >= 5
