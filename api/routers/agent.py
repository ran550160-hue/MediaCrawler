# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from mediacrawler_mcp.dataset_bundle_exporter import DatasetBundleExporter
from mediacrawler_mcp.douyin_finalize import finalize_douyin_collection_task
from mediacrawler_mcp.errors import McpAppError

from ..schemas import (
    AgentDouyinSearchRequest,
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
CAPTCHA_BACKOFF_THRESHOLD = 5
FINALIZABLE_STATUSES = {"completed", "partial_success", "cancelled_partial", "failed_with_data"}

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


def _task_id(platform: str = "xhs") -> str:
    prefix = "agent_douyin" if platform == "douyin" else "agent_xhs"
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"


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


def _count_jsonl_lines(path: Path | None) -> int:
    if not path or not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def _task_progress(task: dict[str, Any]) -> dict[str, Any]:
    request = task.get("request") or {}
    candidates = _candidate_jsonl_files(task)
    contents_count = _count_jsonl_lines(candidates["contents"])
    comments_count = _count_jsonl_lines(candidates["comments"])
    max_contents = int(request.get("max_contents") or 0)
    max_comments_per_content = int(request.get("max_comments_per_content") or 0)
    include_comments = bool(request.get("include_comments", True))
    comments_target = max_contents * max_comments_per_content if include_comments else 0
    contents_complete = max_contents > 0 and contents_count >= max_contents
    comments_complete = comments_target == 0 or comments_count >= comments_target
    return {
        "contents_count": contents_count,
        "comments_count": comments_count,
        "max_contents": max_contents,
        "max_comments_per_content": max_comments_per_content,
        "comments_target": comments_target,
        "contents": f"{contents_count}/{max_contents}" if max_contents else f"{contents_count}/?",
        "comments": f"{comments_count}/{comments_target}" if comments_target else f"{comments_count}/0",
        "contents_complete": contents_complete,
        "comments_complete": comments_complete,
        "target_reached": contents_complete and comments_complete,
        "has_output": contents_count > 0 or comments_count > 0,
    }


def _captcha_backoff_reached(logs: list[dict[str, Any]]) -> bool:
    consecutive = 0
    markers = ("461", "captcha", "验证码", "滑块", "risk control", "风控")
    for log in reversed(logs):
        message = str(log.get("message", "")).lower()
        if any(marker.lower() in message for marker in markers):
            consecutive += 1
            if consecutive >= CAPTCHA_BACKOFF_THRESHOLD:
                return True
            continue
        if consecutive:
            break
    return False


def _stop_guard_reason(task: dict[str, Any], progress: dict[str, Any], logs: list[dict[str, Any]]) -> str | None:
    if progress["target_reached"]:
        return "target_reached"
    timeout_seconds = int((task.get("request") or {}).get("timeout_seconds") or 0)
    if timeout_seconds > 0 and datetime.now() - task["started_at"] >= timedelta(seconds=timeout_seconds):
        return "timeout"
    if _captcha_backoff_reached(logs):
        return "captcha_backoff"
    return None


async def _stop_active_task(task: dict[str, Any], reason: str) -> None:
    global _current_task_id

    if _current_task_id == task["task_id"] and crawler_manager.process and crawler_manager.process.poll() is None:
        await crawler_manager.stop()
        _current_task_id = None

    progress = _task_progress(task)
    target_reached = reason == "target_reached" and progress["has_output"]
    task["status"] = "completed" if target_reached else ("partial_success" if progress["has_output"] else "failed")
    task["completed_at"] = datetime.now()
    task["exit_code"] = 0 if target_reached else crawler_manager.last_exit_code
    platform_label = "Douyin" if task.get("platform") == "douyin" else "XHS"
    task["message"] = f"Local {platform_label} search stopped: {reason}"
    task["stop_reason"] = reason


async def _apply_runtime_guards(task: dict[str, Any]) -> None:
    if task["status"] not in {"accepted", "running", "needs_user_action"}:
        return
    if _current_task_id != task["task_id"] or crawler_manager.status != "running":
        return
    logs = _recent_logs(100)
    progress = _task_progress(task)
    reason = _stop_guard_reason(task, progress, logs)
    if reason:
        await _stop_active_task(task, reason)


def _task_status(task: dict[str, Any]) -> str:
    if task["status"] == "failed" and _task_progress(task)["has_output"]:
        task["status"] = "failed_with_data"
    elif task["status"] == "cancelled" and _task_progress(task)["has_output"]:
        task["status"] = "cancelled_partial"
    if task["status"] in {"failed", "completed", "partial_success", "cancelled", "cancelled_partial", "failed_with_data"}:
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
        progress = _task_progress(task)
        files_available = progress["has_output"]
        if failed:
            task["status"] = "failed_with_data" if files_available else "failed"
        else:
            task["status"] = "completed" if files_available or crawler_manager.last_exit_code == 0 else "failed"
        task["completed_at"] = datetime.now()
        task["exit_code"] = crawler_manager.last_exit_code
    return task["status"]


def _douyin_discover_output(task: dict[str, Any]) -> dict[str, Any]:
    """Discover run-isolated Douyin output containing search contents/comments.

    Returns a dict with contents, comments (Path or None), and run_id.
    Raises HTTPException on multi-run ambiguity.
    """
    data_root = Path(task.get("data_root") or DATA_DIR)
    dy_dir = data_root / "douyin"
    empty = {"contents": None, "comments": None, "run_id": None}
    if not dy_dir.exists():
        return empty
    run_dirs = [
        child for child in dy_dir.iterdir()
        if child.is_dir() and (child / "run_metadata.json").exists()
    ]
    usable: list[Path] = []
    for run in run_dirs:
        try:
            meta = json.loads((run / "run_metadata.json").read_text(encoding="utf-8"))
            if meta.get("platform") != "douyin":
                continue
        except Exception:
            continue
        if (run / "jsonl" / "search_contents.jsonl").exists() or (run / "jsonl" / "search_comments.jsonl").exists():
            usable.append(run)
    if not usable:
        return empty
    if len(usable) > 1:
        raise HTTPException(
            status_code=409,
            detail="Multiple Douyin runs found in output directory; cannot uniquely determine which run to finalize",
        )
    chosen = usable[0]
    contents = chosen / "jsonl" / "search_contents.jsonl"
    comments = chosen / "jsonl" / "search_comments.jsonl"
    return {
        "contents": contents if contents.exists() else None,
        "comments": comments if comments.exists() else None,
        "run_id": chosen.name,
    }


def _douyin_candidate_jsonl_files(task: dict[str, Any]) -> dict[str, Path | None]:
    discovery = _douyin_discover_output(task)
    return {"contents": discovery["contents"], "comments": discovery["comments"]}


def _candidate_jsonl_files(task: dict[str, Any]) -> dict[str, Path | None]:
    platform = task.get("platform", "xhs")
    if platform == "douyin":
        return _douyin_candidate_jsonl_files(task)
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
    progress = _task_progress(task)
    can_finalize = progress["has_output"]
    payload = {
        "task_id": task["task_id"],
        "platform": task.get("platform", "xhs"),
        "status": status,
        "message": task.get("message", ""),
        "keywords": task["keywords"],
        "data_root": task.get("data_root"),
        "started_at": task["started_at"].isoformat(),
        "completed_at": task["completed_at"].isoformat() if task.get("completed_at") else None,
        "exit_code": crawler_manager.last_exit_code if _current_task_id == task["task_id"] else task.get("exit_code"),
        "partial": status in {"partial_success", "cancelled_partial", "failed_with_data"},
        "stop_reason": task.get("stop_reason"),
        "progress": progress,
        "log_summary": _log_summary(logs),
        "files": files,
        "can_finalize": can_finalize,
    }
    if include_logs:
        payload["logs"] = logs
    return payload


async def _start_agent_task(
    request: AgentXHSSearchRequest,
    *,
    data_root_override: Path | None = None,
    retried_from: str | None = None,
):
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

    task_id = _task_id("xhs")
    data_root = data_root_override.resolve() if data_root_override else _task_data_root(task_id)
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
        "platform": "xhs",
        "status": "accepted",
        "keywords": keywords,
        "request": request.model_dump(),
        "dataset_name": request.dataset_name,
        "description": request.description,
        "data_root": str(data_root),
        "retried_from": retried_from,
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
        "retried_from": retried_from,
    }


@router.post("/xhs/search", dependencies=[Depends(_require_agent_token)])
async def start_xhs_search(request: AgentXHSSearchRequest):
    """Start a restricted local XHS search for Hermes/local agents."""
    return await _start_agent_task(request)


async def _start_douyin_agent_task(
    request: AgentDouyinSearchRequest,
    *,
    data_root_override: Path | None = None,
    retried_from: str | None = None,
):
    """Create and start a task-scoped local Douyin search."""
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

    task_id = _task_id("douyin")
    data_root = data_root_override.resolve() if data_root_override else _task_data_root(task_id)
    data_root.mkdir(parents=True, exist_ok=True)
    crawler_request = CrawlerStartRequest(
        platform=PlatformEnum.DOUYIN,
        login_type=LoginTypeEnum.QRCODE,
        crawler_type=CrawlerTypeEnum.SEARCH,
        keywords=",".join(keywords),
        start_page=1,
        enable_comments=request.include_comments,
        enable_sub_comments=request.include_sub_comments,
        save_option=SaveDataOptionEnum.JSONL,
        headless=request.headless,
        max_notes_count=request.max_contents,
        max_comments_count=request.max_comments_per_content if request.include_comments else None,
        enable_cdp_mode=request.enable_cdp_mode,
        cdp_connect_existing=request.cdp_connect_existing,
        cdp_debug_port=request.cdp_debug_port,
        save_data_path=str(data_root),
    )

    task = {
        "task_id": task_id,
        "platform": "douyin",
        "status": "accepted",
        "keywords": keywords,
        "request": request.model_dump(),
        "dataset_name": request.dataset_name,
        "description": request.description,
        "data_root": str(data_root),
        "retried_from": retried_from,
        "started_at": datetime.now(),
        "completed_at": None,
        "exit_code": None,
        "message": "Local Douyin search accepted",
    }
    _tasks[task_id] = task
    _current_task_id = task_id

    success = await crawler_manager.start(crawler_request)
    if not success:
        task["status"] = "failed"
        task["message"] = "Failed to start local Douyin search"
        raise HTTPException(status_code=500, detail=task["message"])

    task["status"] = "running"
    task["message"] = "Local Douyin search started"
    return {
        "task_id": task_id,
        "status": "running",
        "message": task["message"],
        "data_root": str(data_root),
        "retried_from": retried_from,
    }


@router.post("/douyin/search", dependencies=[Depends(_require_agent_token)])
async def start_douyin_search(request: AgentDouyinSearchRequest):
    """Start a restricted local Douyin search for Hermes/local agents."""
    return await _start_douyin_agent_task(request)


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
    await _apply_runtime_guards(task)
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

    platform = task.get("platform", "xhs")

    # Idempotency: return existing finalized result without re-exporting
    if platform != "douyin" and task.get("finalized"):
        return {"status": "success", "already_finalized": True, **task["finalized"]}

    await _apply_runtime_guards(task)
    status = _task_status(task)
    progress = _task_progress(task)
    if status in {"running", "needs_user_action", "accepted"} and progress["has_output"]:
        await _stop_active_task(task, "manual_finalize")
        status = _task_status(task)
        progress = _task_progress(task)
    elif status == "cancelled" and progress["has_output"]:
        task["status"] = "cancelled_partial"
        status = task["status"]
    elif status == "failed" and progress["has_output"]:
        task["status"] = "failed_with_data"
        status = task["status"]

    if status not in FINALIZABLE_STATUSES and not (request.force and progress["has_output"]):
        raise HTTPException(status_code=409, detail=f"Task is not ready to finalize: {status}")

    candidates = _candidate_jsonl_files(task)
    contents_path = candidates["contents"]
    comments_path = candidates["comments"]
    if platform != "douyin" and not contents_path and not comments_path:
        raise HTTPException(status_code=404, detail="No JSONL output files found for this task")

    dataset_name = request.dataset_name or task.get("dataset_name") or f"{'Douyin' if platform == 'douyin' else 'XHS'} search {' '.join(task['keywords'])}"
    description = request.description or task.get("description") or "Exported from local Hermes agent search"

    try:
        if platform == "douyin":
            discovery = _douyin_discover_output(task)
            completed_at = task.get("completed_at") or datetime.now()
            result = finalize_douyin_collection_task(
                task_id=task_id,
                name=dataset_name,
                output_dir=Path(request.output_dir),
                keywords=task["keywords"],
                contents_path=contents_path,
                comments_path=comments_path,
                run_id=discovery.get("run_id"),
                crawler_type="search",
                description=description,
                collection_started_at=task["started_at"].isoformat(),
                collection_completed_at=completed_at.isoformat(),
                dataset_id=request.dataset_id,
            )
        else:
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
                collection_task_id=task_id,
                collection_started_at=task["started_at"].isoformat(),
                collection_completed_at=(task.get("completed_at") or datetime.now()).isoformat(),
            )
    except McpAppError as exc:
        if exc.message == "No Douyin contents JSONL file found for this task":
            raise HTTPException(status_code=404, detail=exc.message) from exc
        raise HTTPException(status_code=400, detail=exc.to_result()) from exc

    task["finalized"] = result
    task["exit_code"] = crawler_manager.last_exit_code if _current_task_id == task["task_id"] else task.get("exit_code")
    return {
        "status": "success",
        **result,
        "task_status": status,
        "partial": status != "completed",
        "progress": progress,
        "files": _task_files(task),
    }



@router.post("/tasks/{task_id}/cancel", dependencies=[Depends(_require_agent_token)])
async def cancel_agent_task(task_id: str):
    global _current_task_id

    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Agent task not found")

    await _apply_runtime_guards(task)
    status = _task_status(task)
    progress = _task_progress(task)
    if status == "cancelled" and progress["has_output"]:
        task["status"] = "cancelled_partial"
        return _task_payload(task)
    if status in {"completed", "partial_success", "failed", "cancelled", "cancelled_partial", "failed_with_data"}:
        return _task_payload(task)

    if _current_task_id == task_id and crawler_manager.process and crawler_manager.process.poll() is None:
        await crawler_manager.stop()
        _current_task_id = None

    progress = _task_progress(task)
    task["status"] = "cancelled_partial" if progress["has_output"] else "cancelled"
    task["completed_at"] = datetime.now()
    task["exit_code"] = crawler_manager.last_exit_code
    task["message"] = "Local XHS search cancelled"
    return _task_payload(task)


@router.post("/tasks/{task_id}/retry", dependencies=[Depends(_require_agent_token)])
async def retry_agent_task(task_id: str, append: bool = True):
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Agent task not found")
    platform = task.get("platform", "xhs")
    data_root = Path(task["data_root"]) if append and task.get("data_root") else None
    if platform == "douyin":
        request = AgentDouyinSearchRequest(**task["request"])
        result = await _start_douyin_agent_task(request, data_root_override=data_root, retried_from=task_id)
    else:
        request = AgentXHSSearchRequest(**task["request"])
        result = await _start_agent_task(request, data_root_override=data_root, retried_from=task_id)
    return {"retried_from": task_id, "append": append, **result}
