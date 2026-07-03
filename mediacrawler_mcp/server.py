from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcp.server.fastmcp import FastMCP

from mediacrawler_mcp.config import load_config
from mediacrawler_mcp.dataset_service import DatasetService
from mediacrawler_mcp.errors import ErrorCode, McpAppError, error_result, success_result
from mediacrawler_mcp.login_manager import LoginManager
from mediacrawler_mcp.normalizer import DatasetNormalizer
from mediacrawler_mcp.qrcode_login import QRCodeLoginManager
from mediacrawler_mcp.query_engine import QueryEngine
from mediacrawler_mcp.report_service import ReportService
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.task_manager import TaskManager
from mediacrawler_mcp.utils import setup_file_logging


mcp = FastMCP("mediacrawler")


def _service() -> DatasetService:
    config = load_config()
    setup_file_logging(config.server_log_path)
    storage = Storage(config)
    return DatasetService(config, storage)


def _storage() -> Storage:
    config = load_config()
    setup_file_logging(config.server_log_path)
    return Storage(config)


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
            sort_by=sort_by,
        )
        return success_result(results=results)
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


@mcp.tool()
def get_login_status(
    platform: str = "xhs",
    account_name: str = "default",
    verify_remote: bool = False,
) -> dict[str, Any]:
    """Get local login status for a platform account."""
    try:
        return LoginManager(_storage()).get_login_status(
            platform=platform,
            account_name=account_name,
            verify_remote=verify_remote,
        )
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to get login status")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to get login status", str(exc))


@mcp.tool()
def import_cookies(
    platform: str,
    cookie_string: str,
    account_name: str = "default",
) -> dict[str, Any]:
    """Import an XHS cookie string for later collection tasks."""
    try:
        return LoginManager(_storage()).import_cookies(
            platform=platform,
            cookie_string=cookie_string,
            account_name=account_name,
        )
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to import cookies")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to import cookies", str(exc))


@mcp.tool()
def start_qrcode_login(
    platform: str = "xhs",
    account_name: str = "default",
    timeout_seconds: int = 120,
    headless: bool = True,
    qr_wait_seconds: int = 30,
) -> dict[str, Any]:
    """Start an XHS QR-code login task and return a QR image path for Feishu."""
    try:
        return QRCodeLoginManager(_storage()).start_qrcode_login(
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


@mcp.tool()
def get_qrcode_login_status(login_task_id: str) -> dict[str, Any]:
    """Get QR-code login task status."""
    try:
        return QRCodeLoginManager(_storage()).get_qrcode_login_status(login_task_id)
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to get QR login status")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to get QR login status", str(exc))


@mcp.tool()
def cancel_qrcode_login(login_task_id: str) -> dict[str, Any]:
    """Cancel an active QR-code login task."""
    try:
        return QRCodeLoginManager(_storage()).cancel_qrcode_login(login_task_id)
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to cancel QR login")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to cancel QR login", str(exc))


@mcp.tool()
def start_collection(
    dataset_id: str,
    include_comments: bool = True,
    max_contents: int = 20,
    max_comments_per_content: int = 10,
    include_sub_comments: bool = False,
    headless: bool = True,
    verify_login_remote: bool = True,
) -> dict[str, Any]:
    """Start an async xhs collection task for a dataset."""
    try:
        task = TaskManager(_storage()).start_collection(
            dataset_id=dataset_id,
            include_comments=include_comments,
            max_contents=max_contents,
            max_comments_per_content=max_comments_per_content,
            include_sub_comments=include_sub_comments,
            headless=headless,
            verify_login_remote=verify_login_remote,
        )
        return {"status": "accepted", **task}
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to start collection")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to start collection", str(exc))


@mcp.tool()
def get_task_status(task_id: str) -> dict[str, Any]:
    """Get a MediaCrawler MCP task status."""
    try:
        return success_result(task=TaskManager(_storage()).get_task_status(task_id))
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to get task status")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to get task status", str(exc))


@mcp.tool()
def cancel_task(task_id: str) -> dict[str, Any]:
    """Cancel a running MediaCrawler MCP task."""
    try:
        return success_result(**TaskManager(_storage()).cancel_task(task_id))
    except McpAppError as exc:
        return exc.to_result()
    except Exception as exc:  # pragma: no cover - safety boundary for MCP tools
        logging.exception("Failed to cancel task")
        return error_result(ErrorCode.INTERNAL_ERROR, "Failed to cancel task", str(exc))


if __name__ == "__main__":
    config = load_config()
    setup_file_logging(config.server_log_path)
    mcp.run("stdio")
