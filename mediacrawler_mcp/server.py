from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcp.server.fastmcp import FastMCP

from mediacrawler_mcp.config import load_config
from mediacrawler_mcp.dataset_importer import DatasetImporter
from mediacrawler_mcp.dataset_service import DatasetService
from mediacrawler_mcp.desktop_agent_client import DesktopAgentClient
from mediacrawler_mcp.errors import ErrorCode, McpAppError, error_result, success_result
from mediacrawler_mcp.normalizer import DatasetNormalizer
from mediacrawler_mcp.query_engine import QueryEngine
from mediacrawler_mcp.report_service import ReportService
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.utils import setup_file_logging


def _apply_cli_profile_overrides(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--profile")
    parser.add_argument("--enable-experimental-collection", action="store_true")
    args, _ = parser.parse_known_args(argv)

    if args.profile in {"dataset", "desktop_agent", "experimental_collection"}:
        os.environ["MEDIACRAWLER_MCP_TOOL_PROFILE"] = args.profile
    if args.enable_experimental_collection:
        os.environ["MEDIACRAWLER_MCP_ENABLE_EXPERIMENTAL_COLLECTION"] = "true"


_apply_cli_profile_overrides(sys.argv[1:])


mcp = FastMCP("mediacrawler")

LoginManager: Any = None
QRCodeLoginManager: Any = None
TaskManager: Any = None


def _service() -> DatasetService:
    config = load_config()
    setup_file_logging(config.server_log_path)
    storage = Storage(config)
    return DatasetService(config, storage)


def _storage() -> Storage:
    config = load_config()
    setup_file_logging(config.server_log_path)
    return Storage(config)


def _importer() -> DatasetImporter:
    config = load_config()
    setup_file_logging(config.server_log_path)
    storage = Storage(config)
    return DatasetImporter(config, storage)


def _login_manager_class() -> Any:
    global LoginManager
    if LoginManager is None:
        from mediacrawler_mcp.login_manager import LoginManager as _LoginManager  # noqa: PLC0415

        LoginManager = _LoginManager
    return LoginManager


def _qrcode_login_manager_class() -> Any:
    global QRCodeLoginManager
    if QRCodeLoginManager is None:
        from mediacrawler_mcp.qrcode_login import QRCodeLoginManager as _QRCodeLoginManager  # noqa: PLC0415

        QRCodeLoginManager = _QRCodeLoginManager
    return QRCodeLoginManager


def _task_manager_class() -> Any:
    global TaskManager
    if TaskManager is None:
        from mediacrawler_mcp.task_manager import TaskManager as _TaskManager  # noqa: PLC0415

        TaskManager = _TaskManager
    return TaskManager


@mcp.tool()
def ping(message: str = "ok") -> str:
    """Return a ping response."""
    return f"pong:{message}"


@mcp.tool()
def create_dataset(
    name: str,
    platforms: list[str],
    keywords: list[str],
    description: str | None = None,
) -> dict[str, Any]:
    """Create a MediaCrawler dataset."""
    try:
        dataset = _service().create_dataset(
            name=name,
            platforms=platforms,
            keywords=keywords,
            description=description,
        )
        return success_result(
            dataset_id=dataset.dataset_id,
            dataset_dir=dataset.dataset_dir,
            dataset_json_path=f"{dataset.dataset_dir}/dataset.json",
        )
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to create dataset")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to create dataset", str(exc))


@mcp.tool()
def list_datasets(
    keyword: str | None = None,
    platform: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """List MediaCrawler datasets."""
    try:
        datasets = _service().list_datasets(
            keyword=keyword,
            platform=platform,
            status=status,
            limit=limit,
        )
        return success_result(datasets=datasets)
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to list datasets")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to list datasets", str(exc))


@mcp.tool()
def register_dataset(dataset_dir: str, import_mode: str = "copy") -> dict[str, Any]:
    """Register a desktop-exported dataset bundle."""
    try:
        return success_result(**_importer().register_dataset(dataset_dir=dataset_dir, import_mode=import_mode))
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to register dataset")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to register dataset", str(exc))


@mcp.tool()
def validate_dataset_bundle(dataset_dir: str) -> dict[str, Any]:
    """Validate a desktop-exported dataset bundle before registration."""
    try:
        return success_result(**_importer().validate_dataset_bundle(dataset_dir=dataset_dir))
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to validate dataset bundle")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to validate dataset bundle", str(exc))


@mcp.tool()
def import_raw_files(
    dataset_id: str,
    platform: str,
    contents_path: str | None = None,
    comments_path: str | None = None,
    source_keyword: str | None = None,
) -> dict[str, Any]:
    """Import raw JSONL files into an existing dataset."""
    try:
        return success_result(
            **_importer().import_raw_files(
                dataset_id=dataset_id,
                platform=platform,
                contents_path=contents_path,
                comments_path=comments_path,
                source_keyword=source_keyword,
            )
        )
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to import raw files")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to import raw files", str(exc))


@mcp.tool()
def sync_dataset_manifest(dataset_id: str) -> dict[str, Any]:
    """Synchronize dataset.json, SQLite metadata, and raw file metadata."""
    try:
        return success_result(**_importer().sync_dataset_manifest(dataset_id=dataset_id))
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to sync dataset manifest")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to sync dataset manifest", str(exc))


@mcp.tool()
def get_dataset(dataset_id: str) -> dict[str, Any]:
    """Get a MediaCrawler dataset."""
    try:
        return success_result(dataset=_service().get_dataset(dataset_id))
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to get dataset")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to get dataset", str(exc))


@mcp.tool()
def normalize_dataset(dataset_id: str, force: bool = False) -> dict[str, Any]:
    """Normalize raw dataset JSONL files into DuckDB."""
    try:
        summary = DatasetNormalizer(_storage()).normalize_dataset(dataset_id, force=force)
        return success_result(**summary)
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to normalize dataset")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to normalize dataset", str(exc))


@mcp.tool()
def query_dataset(
    dataset_id: str,
    query: str,
    target: str = "comments",
    platform: str | None = None,
    source_keyword: str | None = None,
    limit: int = 20,
    offset: int = 0,
    sort_by: str | None = None,
) -> dict[str, Any]:
    """Query an existing normalized MediaCrawler dataset."""
    try:
        results = QueryEngine(_storage()).query_dataset(
            dataset_id=dataset_id,
            query=query,
            target=target,
            platform=platform,
            source_keyword=source_keyword,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
        )
        return success_result(results=results, limit=limit, offset=offset)
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to query dataset")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to query dataset", str(exc))


@mcp.tool()
def generate_report(
    dataset_id: str,
    report_type: str = "topic_research",
    top_n: int = 20,
) -> dict[str, Any]:
    """Generate markdown/html report for a normalized dataset."""
    try:
        report = ReportService(_storage()).generate_report(
            dataset_id=dataset_id,
            report_type=report_type,
            top_n=top_n,
        )
        return success_result(**report)
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to generate report")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to generate report", str(exc))


@mcp.tool()
def get_report(dataset_id: str, report_id: str | None = None) -> dict[str, Any]:
    """Get the latest or specified dataset report."""
    try:
        report = ReportService(_storage()).get_report(dataset_id=dataset_id, report_id=report_id)
        return success_result(report=report)
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to get report")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to get report", str(exc))


def get_login_status(
    platform: str = "xhs",
    account_name: str = "default",
    verify_remote: bool = False,
    verify_permission: bool = False,
) -> dict[str, Any]:
    """Get local login status for a platform account."""
    try:
        return _login_manager_class()(_storage()).get_login_status(
            platform=platform,
            account_name=account_name,
            verify_remote=verify_remote,
            verify_permission=verify_permission,
        )
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to get login status")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to get login status", str(exc))


def import_cookies(
    platform: str,
    cookie_string: str,
    account_name: str = "default",
) -> dict[str, Any]:
    """Import an XHS cookie string for later collection tasks."""
    try:
        return _login_manager_class()(_storage()).import_cookies(
            platform=platform,
            cookie_string=cookie_string,
            account_name=account_name,
        )
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to import cookies")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to import cookies", str(exc))


def start_qrcode_login(
    platform: str = "xhs",
    account_name: str = "default",
    timeout_seconds: int = 120,
    headless: bool = True,
    qr_wait_seconds: int = 30,
) -> dict[str, Any]:
    """Start an XHS QR-code login task and return a QR image path for Feishu."""
    try:
        return _qrcode_login_manager_class()(_storage()).start_qrcode_login(
            platform=platform,
            account_name=account_name,
            timeout_seconds=timeout_seconds,
            headless=headless,
            qr_wait_seconds=qr_wait_seconds,
        )
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to start QR login")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to start QR login", str(exc))


def get_qrcode_login_status(login_task_id: str) -> dict[str, Any]:
    """Get QR-code login task status."""
    try:
        return _qrcode_login_manager_class()(_storage()).get_qrcode_login_status(login_task_id)
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to get QR login status")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to get QR login status", str(exc))


def cancel_qrcode_login(login_task_id: str) -> dict[str, Any]:
    """Cancel an active QR-code login task."""
    try:
        return _qrcode_login_manager_class()(_storage()).cancel_qrcode_login(login_task_id)
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to cancel QR login")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to cancel QR login", str(exc))


def start_collection(
    dataset_id: str,
    include_comments: bool = True,
    max_contents: int = 20,
    max_comments_per_content: int = 10,
    include_sub_comments: bool = False,
    headless: bool = True,
    verify_login_remote: bool = True,
    skip_preflight: bool = False,
) -> dict[str, Any]:
    """Start an async xhs collection task for a dataset."""
    try:
        task = _task_manager_class()(_storage()).start_collection(
            dataset_id=dataset_id,
            include_comments=include_comments,
            max_contents=max_contents,
            max_comments_per_content=max_comments_per_content,
            include_sub_comments=include_sub_comments,
            headless=headless,
            verify_login_remote=verify_login_remote,
            skip_preflight=skip_preflight,
        )
        return {"status": "accepted", **task}
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to start collection")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to start collection", str(exc))


def get_task_status(task_id: str) -> dict[str, Any]:
    """Get a MediaCrawler MCP task status."""
    try:
        return success_result(task=_task_manager_class()(_storage()).get_task_status(task_id))
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to get task status")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to get task status", str(exc))


def cancel_task(task_id: str) -> dict[str, Any]:
    """Cancel a running MediaCrawler MCP task."""
    try:
        return success_result(**_task_manager_class()(_storage()).cancel_task(task_id))
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to cancel task")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to cancel task", str(exc))


EXPERIMENTAL_COLLECTION_TOOLS = (
    get_login_status,
    import_cookies,
    start_qrcode_login,
    get_qrcode_login_status,
    cancel_qrcode_login,
    start_collection,
    get_task_status,
    cancel_task,
)


def check_local_workbench(base_url: str = "http://127.0.0.1:8080") -> dict[str, Any]:
    """Check the Windows MediaCrawler workbench reachable from Hermes/WSL."""
    try:
        client = DesktopAgentClient(base_url=base_url)
        health = client.check_workbench()
        environment = client.check_environment()
        return success_result(base_url=client.base_url, health=health, environment=environment)
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to check local workbench")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to check local workbench", str(exc))


def ensure_cdp_browser(
    port: int = 9222,
    headless: bool = False,
    base_url: str = "http://127.0.0.1:8080",
) -> dict[str, Any]:
    """Ensure a local Chrome/Edge CDP browser is reachable."""
    try:
        return DesktopAgentClient(base_url=base_url).ensure_cdp_browser(port=port, headless=headless)
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to ensure CDP browser")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to ensure CDP browser", str(exc))


def start_local_xhs_search(
    keywords: list[str],
    max_contents: int = 20,
    max_comments_per_content: int = 10,
    include_comments: bool = True,
    include_sub_comments: bool = False,
    cdp_debug_port: int = 9222,
    headless: bool = False,
    base_url: str = "http://127.0.0.1:8080",
) -> dict[str, Any]:
    """Start a restricted Windows-local XHS search task."""
    try:
        return DesktopAgentClient(base_url=base_url).start_xhs_search(
            keywords=keywords,
            max_contents=max_contents,
            max_comments_per_content=max_comments_per_content,
            include_comments=include_comments,
            include_sub_comments=include_sub_comments,
            cdp_debug_port=cdp_debug_port,
            headless=headless,
        )
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to start local XHS search")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to start local XHS search", str(exc))


def get_local_xhs_search_status(
    task_id: str,
    verbose: bool = False,
    log_limit: int = 10,
    include_files: bool = True,
    base_url: str = "http://127.0.0.1:8080",
) -> dict[str, Any]:
    """Get status for a Windows-local XHS search task."""
    try:
        return DesktopAgentClient(base_url=base_url).get_task_status(
            task_id,
            include_logs=verbose,
            include_files=include_files,
            log_limit=log_limit,
        )
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to get local XHS search status")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to get local XHS search status", str(exc))


def cancel_local_xhs_search(
    task_id: str,
    base_url: str = "http://127.0.0.1:8080",
) -> dict[str, Any]:
    """Cancel a Windows-local XHS search task."""
    try:
        return DesktopAgentClient(base_url=base_url).cancel_task(task_id)
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to cancel local XHS search")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to cancel local XHS search", str(exc))


def retry_local_xhs_search(
    task_id: str,
    base_url: str = "http://127.0.0.1:8080",
) -> dict[str, Any]:
    """Retry a Windows-local XHS search task with a fresh task output directory."""
    try:
        return DesktopAgentClient(base_url=base_url).retry_task(task_id)
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to retry local XHS search")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to retry local XHS search", str(exc))


def finalize_local_xhs_search(
    task_id: str,
    normalize: bool = True,
    generate_report: bool = True,
    dataset_name: str = "",
    description: str = "",
    base_url: str = "http://127.0.0.1:8080",
) -> dict[str, Any]:
    """Finalize a Windows-local XHS search into a registered MCP dataset."""
    try:
        client = DesktopAgentClient(base_url=base_url)
        finalized = client.finalize_task(task_id, dataset_name=dataset_name, description=description)
        dataset_dir = finalized.get("dataset_dir")
        if not dataset_dir:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Local finalize did not return dataset_dir")

        registered = _importer().register_dataset(dataset_dir=dataset_dir, import_mode="link")
        dataset_id = registered["dataset_id"]
        normalized = None
        report = None
        if normalize:
            normalized = DatasetNormalizer(_storage()).normalize_dataset(dataset_id, force=True)
        if generate_report:
            report = ReportService(_storage()).generate_report(dataset_id=dataset_id)

        return success_result(
            task_id=task_id,
            dataset_id=dataset_id,
            local_finalize=finalized,
            registered=registered,
            normalized=normalized,
            report=report,
            preview=(finalized.get("files") or [])[:3],
        )
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to finalize local XHS search")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to finalize local XHS search", str(exc))


DESKTOP_AGENT_TOOLS = (
    check_local_workbench,
    ensure_cdp_browser,
    start_local_xhs_search,
    get_local_xhs_search_status,
    cancel_local_xhs_search,
    retry_local_xhs_search,
    finalize_local_xhs_search,
)


def _register_desktop_agent_tools() -> None:
    config = load_config()
    if config.tool_profile != "desktop_agent":
        return
    for tool in DESKTOP_AGENT_TOOLS:
        mcp.tool()(tool)


def _register_experimental_collection_tools() -> None:
    config = load_config()
    if not config.enable_experimental_collection:
        return
    for tool in EXPERIMENTAL_COLLECTION_TOOLS:
        mcp.tool()(tool)


_register_desktop_agent_tools()
_register_experimental_collection_tools()


if __name__ == "__main__":
    config = load_config()
    setup_file_logging(config.server_log_path)
    mcp.run("stdio")
