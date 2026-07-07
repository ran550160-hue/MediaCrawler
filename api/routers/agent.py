# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from mediacrawler_mcp.dataset_bundle_exporter import DatasetBundleExporter
from mediacrawler_mcp.errors import McpAppError

from ..schemas import (
    AgentTaskFinalizeRequest,
    AgentXHSSearchRequest,
    CrawlerStartRequest,
    CrawlerTypeEnum,
    LoginTypeEnum,
    PlatformEnum,
    SaveDataOptionEnum,
)
from ..services import crawler_manager
from .data import DATA_DIR, get_file_info

router = APIRouter(prefix="/agent", tags=["agent"])

MAX_AGENT_CONTENTS = 200
MAX_AGENT_COMMENTS_PER_CONTENT = 200

_tasks: dict[str, dict[str, Any]] = {}
_current_task_id: Optional[str] = None


def _require_agent_token(
    authorization: str | None = Header(default=None),
    x_mediacrawler_agent_token: str | None = Header(default=None),
) -> None:
    expected = os.getenv("MEDIACRAWLER_AGENT_TOKEN")
    if not expected:
        return
    supplied = x_mediacrawler_agent_token
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if supplied != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing agent token")


def _task_id() -> str:
    return f"agent_xhs_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"


def _task_data_root(task_id: str) -> Path:
    return (DATA_DIR / "agent_runs" / task_id).resolve()


def _split_keywords(keywords: list[str]) -> list[str]:
    result: list[str] = []
    for item in keywords:
        result.extend(part.strip() for part in item.split(",") if part.strip())
    return result


def _recent_logs(limit: int = 30) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    return [entry.model_dump() for entry in crawler_manager.logs[-limit:]]


def _looks_like_user_action_needed(logs: list[dict[str, Any]]) -> bool:
    markers = ("扫码", "二维码", "滑块", "验证", "confirm", "confirmation", "scan", "qrcode")
    return any(any(marker.lower() in log["message"].lower() for marker in markers) for log in logs)


def _log_summary(logs: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for log in logs:
        counts[log["level"]] = counts.get(log["level"], 0) + 1
    return {
        "total": len(crawler_manager.logs),
        "counts": counts,
        "last_error": next((log for log in reversed(logs) if log["level"] == "error"), None),
        "last_warning": next((log for log in reversed(logs) if log["level"] == "warning"), None),
        "last_success": next((log for log in reversed(logs) if log["level"] == "success"), None),
    }


def _task_status(task: dict[str, Any]) -> str:
    if task["status"] in {"failed", "completed", "partial_success", "cancelled"}:
        return task["status"]
    if crawler_manager.status == "running" and _current_task_id == task["task_id"]:
        logs = _recent_logs()
        return "needs_user_action" if _looks_like_user_action_needed(logs) else "running"
    if task["status"] in {"accepted", "running", "needs_user_action"}:
        logs = _recent_logs()
        failed = crawler_manager.last_exit_code not in {None, 0} or any(
            "exited with code" in log["message"].lower() or log["level"] == "error"
            for log in logs
        )
        files_available = bool(_task_files(task))
        if failed:
            task["status"] = "partial_success" if files_available else "failed"
        else:
            task["status"] = "completed" if files_available or crawler_manager.last_exit_code == 0 else "failed"
        task["completed_at"] = datetime.now()
        task["exit_code"] = crawler_manager.last_exit_code
    return task["status"]


def _candidate_jsonl_files(task: dict[str, Any]) -> dict[str, Path | None]:
    result: dict[str, Path | None] = {"contents": None, "comments": None}
    data_root = Path(task.get("data_root") or DATA_DIR)
    jsonl_dir = data_root / "xhs" / "jsonl"
    if not jsonl_dir.exists():
        return result

    for kind, pattern in (("contents", "search_contents*.jsonl"), ("comments", "search_comments*.jsonl")):
        candidates = sorted(
            jsonl_dir.glob(pattern),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        result[kind] = candidates[0] if candidates else None
    return result


def _task_files(task: dict[str, Any]) -> list[dict[str, Any]]:
    files = []
    for path in _candidate_jsonl_files(task).values():
        if path and path.exists():
            info = get_file_info(path)
            encoded = "/".join(part for part in info["path"].replace("\\", "/").split("/"))
            files.append(
                {
                    **info,
                    "download_url": f"/api/data/download/{encoded}",
                }
            )
    return files


def _task_payload(
    task: dict[str, Any],
    *,
    include_logs: bool = False,
    include_files: bool = True,
    log_limit: int = 10,
) -> dict[str, Any]:
    status = _task_status(task)
    logs = _recent_logs(log_limit)
    files = _task_files(task) if include_files else []
    payload = {
        "task_id": task["task_id"],
        "status": status,
        "message": task.get("message", ""),
        "keywords": task["keywords"],
        "data_root": task.get("data_root"),
        "started_at": task["started_at"].isoformat(),
        "completed_at": task["completed_at"].isoformat() if task.get("completed_at") else None,
        "exit_code": crawler_manager.last_exit_code if _current_task_id == task["task_id"] else task.get("exit_code"),
        "partial": status == "partial_success",
        "log_summary": _log_summary(logs),
        "files": files,
        "can_finalize": status in {"completed", "partial_success"} and bool(_task_files(task)),
    }
    if include_logs:
        payload["logs"] = logs
    return payload


async def _start_agent_task(request: AgentXHSSearchRequest):
    """Create and start a task-scoped local XHS search."""
    global _current_task_id

    if crawler_manager.process and crawler_manager.process.poll() is None:
        raise HTTPException(status_code=409, detail="Crawler is already running")

    keywords = _split_keywords(request.keywords)
    if not keywords:
        raise HTTPException(status_code=422, detail="At least one keyword is required")
    if request.max_contents > MAX_AGENT_CONTENTS:
        raise HTTPException(status_code=422, detail=f"max_contents must be <= {MAX_AGENT_CONTENTS}")
    if request.max_comments_per_content > MAX_AGENT_COMMENTS_PER_CONTENT:
        raise HTTPException(
            status_code=422,
            detail=f"max_comments_per_content must be <= {MAX_AGENT_COMMENTS_PER_CONTENT}",
        )

    task_id = _task_id()
    data_root = _task_data_root(task_id)
    data_root.mkdir(parents=True, exist_ok=True)
    crawler_request = CrawlerStartRequest(
        platform=PlatformEnum.XHS,
        login_type=LoginTypeEnum.QRCODE,
        crawler_type=CrawlerTypeEnum.SEARCH,
        keywords=",".join(keywords),
        start_page=request.start_page,
        enable_comments=request.include_comments,
        enable_sub_comments=request.include_sub_comments,
        save_option=SaveDataOptionEnum.JSONL,
        headless=request.headless,
        max_notes_count=request.max_contents,
        max_comments_count=request.max_comments_per_content if request.include_comments else None,
        enable_cdp_mode=True,
        cdp_connect_existing=True,
        cdp_debug_port=request.cdp_debug_port,
        save_data_path=str(data_root),
    )

    task = {
        "task_id": task_id,
        "status": "accepted",
        "keywords": keywords,
        "request": request.model_dump(),
        "dataset_name": request.dataset_name,
        "description": request.description,
        "data_root": str(data_root),
        "started_at": datetime.now(),
        "completed_at": None,
        "exit_code": None,
        "message": "Local XHS search accepted",
    }
    _tasks[task_id] = task
    _current_task_id = task_id

    success = await crawler_manager.start(crawler_request)
    if not success:
        task["status"] = "failed"
        task["message"] = "Failed to start local XHS search"
        raise HTTPException(status_code=500, detail=task["message"])

    task["status"] = "running"
    task["message"] = "Local XHS search started"
    return {
        "task_id": task_id,
        "status": "running",
        "message": task["message"],
        "data_root": str(data_root),
    }


@router.post("/xhs/search", dependencies=[Depends(_require_agent_token)])
async def start_xhs_search(request: AgentXHSSearchRequest):
    """Start a restricted local XHS search for Hermes/local agents."""
    return await _start_agent_task(request)


@router.get("/tasks/{task_id}", dependencies=[Depends(_require_agent_token)])
async def get_agent_task(
    task_id: str,
    include_logs: bool = False,
    include_files: bool = True,
    log_limit: int = Query(default=10, ge=0, le=100),
):
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Agent task not found")
    return _task_payload(
        task,
        include_logs=include_logs,
        include_files=include_files,
        log_limit=log_limit,
    )


@router.post("/tasks/{task_id}/finalize", dependencies=[Depends(_require_agent_token)])
async def finalize_agent_task(task_id: str, request: AgentTaskFinalizeRequest):
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Agent task not found")

    status = _task_status(task)
    if status not in {"completed", "partial_success"}:
        raise HTTPException(status_code=409, detail=f"Task is not ready to finalize: {status}")

    candidates = _candidate_jsonl_files(task)
    contents_path = candidates["contents"]
    comments_path = candidates["comments"]
    if not contents_path and not comments_path:
        raise HTTPException(status_code=404, detail="No JSONL output files found for this task")

    dataset_name = request.dataset_name or task.get("dataset_name") or f"XHS 搜索 {' '.join(task['keywords'])}"
    description = request.description or task.get("description") or "Exported from local Hermes agent search"

    try:
        result = DatasetBundleExporter().export_xhs_bundle(
            name=dataset_name,
            output_dir=Path(request.output_dir),
            keywords=task["keywords"],
            description=description,
            data_root=Path(task.get("data_root") or DATA_DIR),
            crawler_type="search",
            contents_path=contents_path,
            comments_path=comments_path,
            dataset_id=request.dataset_id,
        )
    except McpAppError as exc:
        raise HTTPException(status_code=400, detail=exc.to_result()) from exc

    task["finalized"] = result
    task["exit_code"] = crawler_manager.last_exit_code if _current_task_id == task["task_id"] else task.get("exit_code")
    return {
        "status": "success",
        **result,
        "task_status": status,
        "partial": status == "partial_success",
        "files": _task_files(task),
    }


@router.post("/tasks/{task_id}/cancel", dependencies=[Depends(_require_agent_token)])
async def cancel_agent_task(task_id: str):
    global _current_task_id

    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Agent task not found")

    status = _task_status(task)
    if status in {"completed", "partial_success", "failed", "cancelled"}:
        return _task_payload(task)

    if _current_task_id == task_id and crawler_manager.process and crawler_manager.process.poll() is None:
        await crawler_manager.stop()
        _current_task_id = None

    task["status"] = "cancelled"
    task["completed_at"] = datetime.now()
    task["exit_code"] = crawler_manager.last_exit_code
    task["message"] = "Local XHS search cancelled"
    return _task_payload(task)


@router.post("/tasks/{task_id}/retry", dependencies=[Depends(_require_agent_token)])
async def retry_agent_task(task_id: str):
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Agent task not found")
    request = AgentXHSSearchRequest(**task["request"])
    result = await _start_agent_task(request)
    return {"retried_from": task_id, **result}
