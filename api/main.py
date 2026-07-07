# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/main.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

"""
MediaCrawler WebUI API Server
Start command: uvicorn api.main:app --port 8080 --reload
Or: python -m api.main
"""
import asyncio
import os
import subprocess
import socket
from pathlib import Path
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import config

from .routers import agent_router, browser_router, crawler_router, data_router, datasets_router, websocket_router

app = FastAPI(
    title="MediaCrawler WebUI API",
    description="API for controlling MediaCrawler from WebUI",
    version="1.0.0"
)

# Get webui static files directory
WEBUI_DIR = os.path.join(os.path.dirname(__file__), "webui")
REPO_ROOT = Path(__file__).resolve().parents[1]


def _check_result(name: str, status: str, message: str, detail: str | None = None) -> dict:
    result = {"name": name, "status": status, "message": message}
    if detail:
        result["detail"] = detail
    return result


async def _run_command(command: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            cwd=str(REPO_ROOT),
        ),
    )


def _decode_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return value


def _check_cdp_port(port: int) -> dict:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return _check_result(
                "chrome_cdp",
                "ok",
                f"Chrome CDP port {port} is reachable",
            )
    except OSError as exc:
        return _check_result(
            "chrome_cdp",
            "warning",
            f"Chrome CDP port {port} is not reachable",
            str(exc),
        )

# CORS configuration - allow frontend dev server access
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",  # Backup port
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(agent_router, prefix="/api")
app.include_router(browser_router, prefix="/api")
app.include_router(crawler_router, prefix="/api")
app.include_router(data_router, prefix="/api")
app.include_router(datasets_router, prefix="/api")
app.include_router(websocket_router, prefix="/api")


@app.get("/")
async def serve_frontend():
    """Return frontend page"""
    index_path = os.path.join(WEBUI_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {
        "message": "MediaCrawler WebUI API",
        "version": "1.0.0",
        "docs": "/docs",
        "note": "WebUI not found, please build it first: cd webui && npm run build"
    }


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


@app.get("/api/env/check")
async def check_environment(cdp_port: int | None = None):
    """Check if MediaCrawler environment is configured correctly"""
    checks = []
    help_output = ""

    try:
        uv_process = await _run_command(["uv", "--version"], timeout=10.0)
        uv_output = _decode_output(uv_process.stdout or uv_process.stderr).strip()
        if uv_process.returncode == 0:
            checks.append(_check_result("uv", "ok", "uv is available", uv_output[:200]))
        else:
            checks.append(_check_result("uv", "error", "uv command failed", uv_output[:500]))
    except FileNotFoundError:
        checks.append(
            _check_result(
                "uv",
                "error",
                "uv command not found",
                "Please ensure uv is installed and configured in system PATH",
            )
        )
    except subprocess.TimeoutExpired:
        checks.append(_check_result("uv", "error", "uv check timed out"))
    except Exception as exc:
        checks.append(_check_result("uv", "error", "uv check failed", f"{type(exc).__name__}: {exc}"))

    if checks[-1]["name"] == "uv" and checks[-1]["status"] == "ok":
        try:
            help_process = await _run_command(["uv", "run", "main.py", "--help"], timeout=30.0)
            stdout = _decode_output(help_process.stdout)
            stderr = _decode_output(help_process.stderr)
            help_output = stdout[:500]
            if help_process.returncode == 0:
                checks.append(
                    _check_result(
                        "mediacrawler",
                        "ok",
                        "MediaCrawler CLI is available",
                        stdout[:200],
                    )
                )
            else:
                checks.append(
                    _check_result(
                        "mediacrawler",
                        "error",
                        "MediaCrawler CLI check failed",
                        (stderr or stdout)[:500],
                    )
                )
        except subprocess.TimeoutExpired:
            checks.append(_check_result("mediacrawler", "error", "MediaCrawler CLI check timed out"))
        except Exception as exc:
            checks.append(
                _check_result(
                    "mediacrawler",
                    "error",
                    "MediaCrawler CLI check failed",
                    f"{type(exc).__name__}: {exc}",
                )
            )

    try:
        node_process = await _run_command(["node", "--version"], timeout=10.0)
        node_output = _decode_output(node_process.stdout or node_process.stderr).strip()
        if node_process.returncode == 0:
            checks.append(_check_result("node", "ok", "Node.js is available", node_output[:200]))
        else:
            checks.append(_check_result("node", "warning", "Node.js command failed", node_output[:500]))
    except FileNotFoundError:
        checks.append(
            _check_result(
                "node",
                "warning",
                "Node.js command not found",
                "Douyin and Zhihu crawling may require Node.js >= 16",
            )
        )
    except subprocess.TimeoutExpired:
        checks.append(_check_result("node", "warning", "Node.js check timed out"))
    except Exception as exc:
        checks.append(_check_result("node", "warning", "Node.js check failed", f"{type(exc).__name__}: {exc}"))

    checks.append(_check_cdp_port(cdp_port or config.CDP_DEBUG_PORT))

    data_dir = REPO_ROOT / "data"
    if data_dir.exists():
        checks.append(_check_result("data_dir", "ok", "Data directory exists", str(data_dir)))
    else:
        checks.append(_check_result("data_dir", "warning", "Data directory does not exist yet", str(data_dir)))

    webui_dir = Path(WEBUI_DIR)
    if (webui_dir / "index.html").exists():
        checks.append(_check_result("webui", "ok", "WebUI static files are available", str(webui_dir)))
    else:
        checks.append(_check_result("webui", "error", "WebUI index.html is missing", str(webui_dir)))

    success = all(check["status"] != "error" for check in checks)
    response = {
        "success": success,
        "message": "MediaCrawler environment configured correctly" if success else "Environment check failed",
        "checks": checks,
    }
    if help_output:
        response["output"] = help_output
    if not success:
        first_error = next((check for check in checks if check["status"] == "error"), None)
        if first_error:
            response["error"] = first_error.get("detail") or first_error["message"]
    return response


@app.get("/api/config/platforms")
async def get_platforms():
    """Get list of supported platforms"""
    return {
        "platforms": [
            {"value": "xhs", "label": "Xiaohongshu", "icon": "book-open"},
            {"value": "dy", "label": "Douyin", "icon": "music"},
            {"value": "ks", "label": "Kuaishou", "icon": "video"},
            {"value": "bili", "label": "Bilibili", "icon": "tv"},
            {"value": "wb", "label": "Weibo", "icon": "message-circle"},
            {"value": "tieba", "label": "Baidu Tieba", "icon": "messages-square"},
            {"value": "zhihu", "label": "Zhihu", "icon": "help-circle"},
        ]
    }


@app.get("/api/config/options")
async def get_config_options():
    """Get all configuration options"""
    return {
        "login_types": [
            {"value": "qrcode", "label": "QR Code Login"},
            {"value": "cookie", "label": "Cookie Login"},
        ],
        "crawler_types": [
            {"value": "search", "label": "Search Mode"},
            {"value": "detail", "label": "Detail Mode"},
            {"value": "creator", "label": "Creator Mode"},
        ],
        "save_options": [
            {"value": "jsonl", "label": "JSONL File"},
            {"value": "json", "label": "JSON File"},
            {"value": "csv", "label": "CSV File"},
            {"value": "excel", "label": "Excel File"},
            {"value": "sqlite", "label": "SQLite Database"},
            {"value": "db", "label": "MySQL Database"},
            {"value": "mongodb", "label": "MongoDB Database"},
        ],
    }


# Mount static resources - must be placed after all routes
if os.path.exists(WEBUI_DIR):
    assets_dir = os.path.join(WEBUI_DIR, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
    # Mount logos directory
    logos_dir = os.path.join(WEBUI_DIR, "logos")
    if os.path.exists(logos_dir):
        app.mount("/logos", StaticFiles(directory=logos_dir), name="logos")
    # Mount other static files (e.g., vite.svg)
    app.mount("/static", StaticFiles(directory=WEBUI_DIR), name="webui-static")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
