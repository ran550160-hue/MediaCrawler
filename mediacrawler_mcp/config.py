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
    tool_profile: str = "dataset"
    enable_experimental_collection: bool = False

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
    def login_qrcodes_dir(self) -> Path:
        return self.home / "login_qrcodes"

    @property
    def locks_dir(self) -> Path:
        return self.home / "locks"

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


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> McpConfig:
    home = Path(os.getenv("MEDIACRAWLER_MCP_HOME", str(DEFAULT_HOME))).expanduser()
    cdp_endpoint = os.getenv("MEDIACRAWLER_MCP_CDP_ENDPOINT") or None
    tool_profile = os.getenv("MEDIACRAWLER_MCP_TOOL_PROFILE", "dataset").strip().lower() or "dataset"
    return McpConfig(
        home=home,
        browser_mode=os.getenv("MEDIACRAWLER_MCP_BROWSER_MODE", "persistent_context"),
        cdp_endpoint=cdp_endpoint,
        max_concurrent_tasks=_int_env("MEDIACRAWLER_MCP_MAX_CONCURRENT_TASKS", 1),
        default_timeout_seconds=_int_env("MEDIACRAWLER_MCP_DEFAULT_TIMEOUT_SECONDS", 300),
        tool_profile=tool_profile,
        enable_experimental_collection=_bool_env("MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION", False)
        or tool_profile == "experimental_collection",
    )
