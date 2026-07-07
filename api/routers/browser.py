# -*- coding: utf-8 -*-
import socket
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException

import config
from tools.browser_launcher import BrowserLauncher

from ..schemas import CDPBrowserStartRequest

router = APIRouter(prefix="/browser", tags=["browser"])

REPO_ROOT = Path(__file__).resolve().parents[2]

_active_launcher: Optional[BrowserLauncher] = None
_active_port: Optional[int] = None


def _is_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _get_cdp_version(port: int) -> dict:
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
            import json

            return json.loads(response.read().decode("utf-8", errors="ignore"))
    except (OSError, URLError, ValueError):
        return {}


def _current_process_info() -> dict:
    if not _active_launcher or not _active_launcher.browser_process:
        return {}
    process = _active_launcher.browser_process
    return {
        "pid": process.pid,
        "launched_by_webui": process.poll() is None,
    }


def _status_for_port(port: int) -> dict:
    reachable = _is_port_open(port)
    version = _get_cdp_version(port) if reachable else {}
    return {
        "status": "running" if reachable else "stopped",
        "port": port,
        "endpoint": f"http://127.0.0.1:{port}",
        "reachable": reachable,
        "browser": version.get("Browser"),
        "web_socket_debugger_url": version.get("webSocketDebuggerUrl"),
        **_current_process_info(),
    }


def _resolve_browser_path(request: CDPBrowserStartRequest, launcher: BrowserLauncher) -> str:
    browser_path = request.browser_path.strip() or config.CUSTOM_BROWSER_PATH
    if browser_path:
        path = Path(browser_path).expanduser()
        if not path.is_file():
            raise HTTPException(status_code=400, detail=f"Browser path does not exist: {browser_path}")
        return str(path)

    paths = launcher.detect_browser_paths()
    if not paths:
        raise HTTPException(
            status_code=400,
            detail="No Chrome or Edge browser found. Set a custom browser path and try again.",
        )
    return paths[0]


@router.get("/cdp/status")
async def get_cdp_browser_status(port: int = 9222):
    """Get Chrome/Edge CDP port status."""
    if port < 1 or port > 65535:
        raise HTTPException(status_code=422, detail="port must be between 1 and 65535")
    return _status_for_port(port)


@router.post("/cdp/start")
async def start_cdp_browser(request: CDPBrowserStartRequest):
    """Launch a local Chrome/Edge browser with CDP remote debugging enabled."""
    global _active_launcher, _active_port

    if _is_port_open(request.port):
        return {
            **_status_for_port(request.port),
            "status": "already_running",
            "message": f"CDP browser is already reachable on port {request.port}",
        }

    launcher = BrowserLauncher()
    browser_path = _resolve_browser_path(request, launcher)
    debug_port = launcher.find_available_port(request.port)
    user_data_dir = (
        Path(request.user_data_dir).expanduser()
        if request.user_data_dir.strip()
        else REPO_ROOT / "browser_data" / "webui_cdp_user_data_dir"
    )
    user_data_dir.mkdir(parents=True, exist_ok=True)

    try:
        launcher.launch_browser(
            browser_path=browser_path,
            debug_port=debug_port,
            headless=request.headless,
            user_data_dir=str(user_data_dir),
        )
        if not launcher.wait_for_browser_ready(debug_port, request.timeout_seconds):
            launcher.cleanup()
            raise HTTPException(
                status_code=500,
                detail=f"Browser failed to start within {request.timeout_seconds} seconds",
            )
    except HTTPException:
        raise
    except Exception as exc:
        launcher.cleanup()
        raise HTTPException(status_code=500, detail=f"Failed to start CDP browser: {exc}") from exc

    _active_launcher = launcher
    _active_port = debug_port
    return {
        **_status_for_port(debug_port),
        "status": "started",
        "message": f"CDP browser started on port {debug_port}",
        "browser_path": browser_path,
        "user_data_dir": str(user_data_dir),
        "requested_port": request.port,
    }
