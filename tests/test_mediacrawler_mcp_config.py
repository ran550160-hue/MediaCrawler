from pathlib import Path

from mediacrawler_mcp.config import DEFAULT_HOME, load_config


def test_load_config_uses_default_home(monkeypatch):
    monkeypatch.delenv("MEDIACRAWLER_MCP_HOME", raising=False)

    config = load_config()

    assert config.home == DEFAULT_HOME
    assert config.datasets_dir == DEFAULT_HOME / "datasets"
    assert config.metadata_db_path == DEFAULT_HOME / "metadata.sqlite"
    assert config.server_log_path == DEFAULT_HOME / "logs" / "server.log"


def test_load_config_uses_environment_home(monkeypatch, tmp_path):
    custom_home = tmp_path / "mcp-home"
    monkeypatch.setenv("MEDIACRAWLER_MCP_HOME", str(custom_home))

    config = load_config()

    assert config.home == custom_home
    assert config.datasets_dir == custom_home / "datasets"
    assert config.metadata_db_path == custom_home / "metadata.sqlite"


def test_load_config_reads_optional_environment_values(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIACRAWLER_MCP_HOME", str(tmp_path))
    monkeypatch.setenv("MEDIACRAWLER_MCP_BROWSER_MODE", "cdp")
    monkeypatch.setenv("MEDIACRAWLER_MCP_CDP_ENDPOINT", "http://127.0.0.1:9222")
    monkeypatch.setenv("MEDIACRAWLER_MCP_MAX_CONCURRENT_TASKS", "2")
    monkeypatch.setenv("MEDIACRAWLER_MCP_DEFAULT_TIMEOUT_SECONDS", "120")

    config = load_config()

    assert config.browser_mode == "cdp"
    assert config.cdp_endpoint == "http://127.0.0.1:9222"
    assert config.max_concurrent_tasks == 2
    assert config.default_timeout_seconds == 120
