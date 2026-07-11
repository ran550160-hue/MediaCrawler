# -*- coding: utf-8 -*-
import socket
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request
from urllib.error import URLError
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException

import config
from tools.browser_launcher import BrowserLauncher

from ..schemas import CDPBrowserOpenRequest, CDPBrowserStartRequest

router = APIRouter(prefix="/browser", tags=["browser"])

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CDP_USER_DATA_DIR = REPO_ROOT / "browser_data" / "xhs_cdp_profile"
DEFAULT_XHS_URL = "https://www.xiaohongshu.com/explore"

_active_launcher: Optional[BrowserLauncher] = None
_active_port: Optional[int] = None
_active_user_data_dir: Optional[Path] = None


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


def _open_cdp_url(port: int, url: str) -> dict:
    encoded_url = quote(url, safe="")
    endpoint = f"http://127.0.0.1:{port}/json/new?{encoded_url}"
    try:
        request = Request(endpoint, method="PUT")
        with urlopen(request, timeout=5) as response:
            import json

            return json.loads(response.read().decode("utf-8", errors="ignore"))
    except HTTPError as exc:
        if exc.code != 405:
            raise
        with urlopen(endpoint, timeout=5) as response:
            import json

            return json.loads(response.read().decode("utf-8", errors="ignore"))


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
        "user_data_dir": str(_active_user_data_dir) if _active_port == port and _active_user_data_dir else None,
        "default_user_data_dir": str(DEFAULT_CDP_USER_DATA_DIR),
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


def _resolve_user_data_dir(user_data_dir: str) -> Path:
    if user_data_dir.strip():
        return Path(user_data_dir).expanduser().resolve()
    return DEFAULT_CDP_USER_DATA_DIR


@router.get("/cdp/status")
async def get_cdp_browser_status(port: int = 9222):
    """Get Chrome/Edge CDP port status."""
    if port < 1 or port > 65535:
        raise HTTPException(status_code=422, detail="port must be between 1 and 65535")
    return _status_for_port(port)


@router.post("/cdp/start")
async def start_cdp_browser(request: CDPBrowserStartRequest):
    """Launch a local Chrome/Edge browser with CDP remote debugging enabled."""
    global _active_launcher, _active_port, _active_user_data_dir

    if _is_port_open(request.port):
        open_result = None
        if request.start_url.strip():
            try:
                open_result = _open_cdp_url(request.port, request.start_url.strip())
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Failed to open start URL: {exc}") from exc
        return {
            **_status_for_port(request.port),
            "status": "already_running",
            "message": f"CDP browser is already reachable on port {request.port}",
            "opened": open_result,
        }

    launcher = BrowserLauncher()
    browser_path = _resolve_browser_path(request, launcher)
    debug_port = launcher.find_available_port(request.port)
    user_data_dir = _resolve_user_data_dir(request.user_data_dir)
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
        open_result = None
        if request.start_url.strip():
            open_result = _open_cdp_url(debug_port, request.start_url.strip())
    except HTTPException:
        raise
    except Exception as exc:
        launcher.cleanup()
        raise HTTPException(status_code=500, detail=f"Failed to start CDP browser: {exc}") from exc

    _active_launcher = launcher
    _active_port = debug_port
    _active_user_data_dir = user_data_dir
    return {
        **_status_for_port(debug_port),
        "status": "started",
        "message": f"CDP browser started on port {debug_port}",
        "browser_path": browser_path,
        "user_data_dir": str(user_data_dir),
        "opened": open_result,
        "requested_port": request.port,
    }


@router.post("/cdp/open")
async def open_cdp_browser_url(request: CDPBrowserOpenRequest):
    """Open a URL in an existing local CDP browser."""
    url = request.url.strip() or DEFAULT_XHS_URL
    if not _is_port_open(request.port):
        raise HTTPException(status_code=409, detail=f"CDP browser is not reachable on port {request.port}")
    try:
        opened = _open_cdp_url(request.port, url)
    except (OSError, URLError, HTTPError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to open URL in CDP browser: {exc}") from exc
    return {
        **_status_for_port(request.port),
        "status": "opened",
        "url": url,
        "opened": opened,
        "message": f"Opened URL in CDP browser on port {request.port}",
    }
