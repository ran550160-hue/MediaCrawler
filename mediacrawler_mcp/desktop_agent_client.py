from __future__ import annotations

import os
import posixpath
import re
from typing import Any

import httpx

from mediacrawler_mcp.errors import ErrorCode, McpAppError


DEFAULT_BASE_URL = "http://127.0.0.1:8080"


def desktop_base_url(value: str | None = None) -> str:
    return (value or os.getenv("MEDIACRAWLER_DESKTOP_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def windows_path_to_wsl(path: str | None) -> str | None:
    if not path:
        return path
    value = str(path)
    windows_root = os.getenv("MEDIACRAWLER_DESKTOP_WINDOWS_ROOT")
    wsl_root = os.getenv("MEDIACRAWLER_DESKTOP_WSL_ROOT")
    if windows_root and wsl_root:
        normalized_root = windows_root.rstrip("\\/")
        if value.lower().startswith(normalized_root.lower()):
            suffix = value[len(normalized_root) :].lstrip("\\/")
            return posixpath.join(wsl_root.rstrip("/"), suffix.replace("\\", "/"))

    match = re.match(r"^([A-Za-z]):[\\/](.*)$", value)
    if match:
        drive = match.group(1).lower()
        suffix = match.group(2).replace("\\", "/")
        return f"/mnt/{drive}/{suffix}"
    return value.replace("\\", "/")


def convert_windows_paths(payload: Any) -> Any:
    if isinstance(payload, dict):
        converted = {}
        for key, value in payload.items():
            if isinstance(value, str) and (
                key.endswith("_path")
                or key.endswith("_dir")
                or key.endswith("_root")
                or key in {"path", "dataset_dir", "dataset_json_path"}
            ):
                converted[key] = windows_path_to_wsl(value)
            else:
                converted[key] = convert_windows_paths(value)
        return converted
    if isinstance(payload, list):
        return [convert_windows_paths(item) for item in payload]
    return payload


class DesktopAgentClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout_seconds: float = 30.0):
        self.base_url = desktop_base_url(base_url)
        self.token = token if token is not None else os.getenv("MEDIACRAWLER_AGENT_TOKEN")
        self.timeout_seconds = timeout_seconds

    def check_workbench(self) -> dict[str, Any]:
        return self._request("GET", "/api/health")

    def check_environment(self, cdp_port: int = 9222) -> dict[str, Any]:
        return self._request("GET", f"/api/env/check?cdp_port={cdp_port}")

    def ensure_cdp_browser(
        self,
        port: int = 9222,
        headless: bool = False,
        user_data_dir: str = "",
        start_url: str = "",
    ) -> dict[str, Any]:
        status = self._request("GET", f"/api/browser/cdp/status?port={port}")
        if status.get("reachable"):
            opened = None
            if start_url:
                opened = self._request(
                    "POST",
                    "/api/browser/cdp/open",
                    json={"port": port, "url": start_url},
                )
            return convert_windows_paths(
                {
                    "status": "success",
                    "message": "CDP browser is already reachable",
                    **status,
                    "opened": opened,
                }
            )
        result = self._request(
            "POST",
            "/api/browser/cdp/start",
            json={
                "port": port,
                "headless": headless,
                "user_data_dir": user_data_dir,
                "start_url": start_url,
            },
        )
        return convert_windows_paths({"status": "success", **result})

    def start_xhs_search(
        self,
        keywords: list[str],
        max_contents: int = 20,
        max_comments_per_content: int = 10,
        include_comments: bool = True,
        include_sub_comments: bool = False,
        cdp_debug_port: int = 9222,
        headless: bool = False,
        timeout_seconds: int = 1800,
    ) -> dict[str, Any]:
        return convert_windows_paths(
            self._request(
                "POST",
                "/api/agent/xhs/search",
                json={
                    "keywords": keywords,
                    "max_contents": max_contents,
                    "max_comments_per_content": max_comments_per_content,
                    "include_comments": include_comments,
                    "include_sub_comments": include_sub_comments,
                    "cdp_debug_port": cdp_debug_port,
                    "headless": headless,
                    "timeout_seconds": timeout_seconds,
                },
            )
        )

    def start_douyin_search(
        self,
        keywords: list[str],
        max_contents: int = 10,
        max_comments_per_content: int = 3,
        include_comments: bool = True,
        include_sub_comments: bool = False,
        enable_cdp_mode: bool = False,
        cdp_connect_existing: bool = False,
        cdp_debug_port: int = 9222,
        headless: bool = False,
        timeout_seconds: int = 1800,
    ) -> dict[str, Any]:
        return convert_windows_paths(
            self._request(
                "POST",
                "/api/agent/douyin/search",
                json={
                    "keywords": keywords,
                    "max_contents": max_contents,
                    "max_comments_per_content": max_comments_per_content,
                    "include_comments": include_comments,
                    "include_sub_comments": include_sub_comments,
                    "enable_cdp_mode": enable_cdp_mode,
                    "cdp_connect_existing": cdp_connect_existing,
                    "cdp_debug_port": cdp_debug_port,
                    "headless": headless,
                    "timeout_seconds": timeout_seconds,
                },
            )
        )

    def get_task_status(
        self,
        task_id: str,
        include_logs: bool = False,
        include_files: bool = True,
        log_limit: int = 10,
    ) -> dict[str, Any]:
        return convert_windows_paths(
            self._request(
                "GET",
                f"/api/agent/tasks/{task_id}",
                params={
                    "include_logs": include_logs,
                    "include_files": include_files,
                    "log_limit": log_limit,
                },
            )
        )

    def finalize_task(
        self,
        task_id: str,
        dataset_name: str = "",
        description: str = "",
        force: bool = False,
        report_type: str = "none",
    ) -> dict[str, Any]:
        return convert_windows_paths(
            self._request(
                "POST",
                f"/api/agent/tasks/{task_id}/finalize",
                json={
                    "dataset_name": dataset_name,
                    "description": description,
                    "force": force,
                    "report_type": report_type,
                },
            )
        )

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        return convert_windows_paths(self._request("POST", f"/api/agent/tasks/{task_id}/cancel"))

    def retry_task(self, task_id: str, append: bool = True) -> dict[str, Any]:
        return convert_windows_paths(
            self._request(
                "POST",
                f"/api/agent/tasks/{task_id}/retry",
                params={"append": append},
            )
        )

    def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    json=json,
                    params=params,
                )
        except httpx.TimeoutException as exc:
            raise McpAppError(ErrorCode.NETWORK_TIMEOUT, "Local workbench request timed out", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise McpAppError(ErrorCode.INTERNAL_ERROR, "Local workbench request failed", str(exc)) from exc

        if response.status_code >= 400:
            detail: Any
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise McpAppError(
                ErrorCode.INTERNAL_ERROR,
                "Local workbench returned an error",
                f"HTTP {response.status_code}: {detail}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise McpAppError(ErrorCode.INTERNAL_ERROR, "Local workbench returned non-JSON response", response.text) from exc
        return payload
