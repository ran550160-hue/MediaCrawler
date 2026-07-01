from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_HOME = Path.home() / ".mediacrawler-mcp"


@dataclass(frozen=True, slots=True)
class McpConfig:
    home: Path
    browser_mode: str
    cdp_endpoint: str | None
    max_concurrent_tasks: int
    default_timeout_seconds: int

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def server_log_path(self) -> Path:
        return self.logs_dir / "server.log"

    @property
    def datasets_dir(self) -> Path:
        return self.home / "datasets"

    @property
    def accounts_dir(self) -> Path:
        return self.home / "accounts"

    @property
    def metadata_db_path(self) -> Path:
        return self.home / "metadata.sqlite"


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def load_config() -> McpConfig:
    home = Path(os.getenv("MEDIACRAWLER_MCP_HOME", str(DEFAULT_HOME))).expanduser()
    cdp_endpoint = os.getenv("MEDIACRAWLER_MCP_CDP_ENDPOINT") or None
    return McpConfig(
        home=home,
        browser_mode=os.getenv("MEDIACRAWLER_MCP_BROWSER_MODE", "persistent_context"),
        cdp_endpoint=cdp_endpoint,
        max_concurrent_tasks=_int_env("MEDIACRAWLER_MCP_MAX_CONCURRENT_TASKS", 1),
        default_timeout_seconds=_int_env("MEDIACRAWLER_MCP_DEFAULT_TIMEOUT_SECONDS", 300),
    )
