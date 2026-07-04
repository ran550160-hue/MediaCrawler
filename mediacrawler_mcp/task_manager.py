from __future__ import annotations

import json
import socket
import subprocess
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mediacrawler_mcp.crawler_runner import CollectionOptions, CrawlerRunner
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.login_manager import LoginManager
from mediacrawler_mcp.locks import acquire_xhs_profile, current_xhs_profile_owner, release_xhs_profile
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.utils import make_task_id, utc_now_iso


_RUNNING: dict[str, subprocess.Popen] = {}


class TaskManager:
    def __init__(
        self,
        storage: Storage,
        runner: CrawlerRunner | None = None,
        login_manager: LoginManager | None = None,
    ):
        self.storage = storage
        self.runner = runner or CrawlerRunner()
        self.login_manager = login_manager or LoginManager(storage)

    def start_collection(
        self,
        dataset_id: str,
        include_comments: bool = True,
        max_contents: int = 20,
        max_comments_per_content: int = 10,
        include_sub_comments: bool = False,
        headless: bool = True,
        verify_login_remote: bool = True,
    ) -> dict[str, Any]:
        dataset_id = (dataset_id or "").strip()
        if not dataset_id:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Dataset ID is required")

        self.storage.initialize()
        dataset = self.storage.get_dataset_row(dataset_id)
        if dataset is None:
            raise McpAppError(ErrorCode.DATASET_NOT_FOUND, "Dataset not found", f"dataset_id={dataset_id}")

        platforms = json.loads(dataset["platforms_json"])
        if platforms != ["xhs"]:
            raise McpAppError(ErrorCode.UNSUPPORTED_PLATFORM, "Only xhs collection is supported")

        keywords = json.loads(dataset["keywords_json"])
        dataset_dir = Path(dataset["dataset_dir"])
        output_dir = dataset_dir / "logs"
        checks, login_status, cdp_debug_port = self._preflight(
            keywords=keywords,
            output_dir=output_dir,
            verify_login_remote=verify_login_remote,
        )
        failed_checks = [check for check in checks if check["status"] == "failed"]
        if failed_checks:
            raise McpAppError(
                ErrorCode.PREFLIGHT_FAILED,
                "Collection preflight failed",
                failed_checks[0]["message"],
                payload={"checks": checks},
            )
        cookie_string = self.login_manager.get_cookie_string("xhs")
        enable_cdp_mode, cdp_connect_existing = self._browser_mode_options()
        options = CollectionOptions(
            include_comments=include_comments,
            max_contents=max(1, int(max_contents or 20)),
            max_comments_per_content=max(0, int(max_comments_per_content or 10)),
            include_sub_comments=include_sub_comments,
            headless=headless,
            login_type="cookie" if cookie_string else "qrcode",
            cookie_string=cookie_string,
            enable_cdp_mode=enable_cdp_mode,
            cdp_connect_existing=cdp_connect_existing,
            cdp_debug_port=cdp_debug_port,
        )

        task_id = make_task_id("collect_xhs")
        lock_owner = f"collection:{task_id}"
        if not acquire_xhs_profile(self.storage.config, lock_owner):
            raise McpAppError(
                ErrorCode.RESOURCE_BUSY,
                "XHS browser profile is busy",
                f"Current owner: {current_xhs_profile_owner(self.storage.config)}",
            )
        output_dir = dataset_dir / "logs" / task_id / "output"
        log_path = dataset_dir / "logs" / f"{task_id}.log"
        now = utc_now_iso()
        self.storage.create_task(
            task_id=task_id,
            dataset_id=dataset_id,
            task_type="collect_xhs",
            status="accepted",
            platforms=platforms,
            keywords=keywords,
            options=options.to_dict(),
            log_path=str(log_path),
            created_at=now,
            updated_at=now,
        )

        try:
            process = self.runner.start(
                keywords=keywords,
                output_dir=output_dir,
                log_path=log_path,
                options=options,
            )
        except Exception:
            release_xhs_profile(lock_owner)
            raise
        _RUNNING[task_id] = process
        self.storage.update_task(
            task_id,
            status="running",
            progress=0.1,
            pid=process.pid,
            started_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )

        thread = threading.Thread(
            target=self._wait_for_collection,
            args=(task_id, process, output_dir, dataset_dir / "raw", options, lock_owner),
            daemon=True,
        )
        thread.start()

        return {
            "task_id": task_id,
            "dataset_id": dataset_id,
            "log_path": str(log_path),
            "message": "Collection task started",
        }

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        task_id = (task_id or "").strip()
        if not task_id:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Task ID is required")
        if task_id.startswith("task_qrcode_login_xhs_"):
            raise McpAppError(
                ErrorCode.TASK_TYPE_MISMATCH,
                "This looks like a QR login task id. Use get_qrcode_login_status instead.",
                f"task_id={task_id}",
            )
        self.storage.initialize()
        row = self.storage.get_task_row(task_id)
        if row is None:
            raise McpAppError(ErrorCode.TASK_NOT_FOUND, "Task not found", f"task_id={task_id}")
        return {
            "task_id": row["task_id"],
            "dataset_id": row["dataset_id"],
            "task_type": row["task_type"],
            "status": row["status"],
            "progress": row["progress"],
            "pid": row["pid"],
            "log_path": row["log_path"],
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "updated_at": row["updated_at"],
        }

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        task_id = (task_id or "").strip()
        if not task_id:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Task ID is required")
        self.storage.initialize()
        row = self.storage.get_task_row(task_id)
        if row is None:
            raise McpAppError(ErrorCode.TASK_NOT_FOUND, "Task not found", f"task_id={task_id}")

        process = _RUNNING.get(task_id)
        if process and process.poll() is None:
            process.terminate()
        now = utc_now_iso()
        self.storage.update_task(
            task_id,
            status="cancelled",
            progress=1.0,
            finished_at=now,
            updated_at=now,
        )
        return {"task_id": task_id, "status": "cancelled"}

    def _wait_for_collection(
        self,
        task_id: str,
        process: subprocess.Popen,
        output_dir: Path,
        raw_dir: Path,
        options: CollectionOptions,
        lock_owner: str,
    ) -> None:
        try:
            return_code = process.wait()
            _RUNNING.pop(task_id, None)
            current = self.storage.get_task_row(task_id)
            if current and current["status"] == "cancelled":
                return
            now = utc_now_iso()
            if return_code != 0:
                error_code, error_message = self._extract_crawler_error(log_path=Path(current["log_path"]) if current else None)
                self.storage.update_task(
                    task_id,
                    status="failed",
                    progress=1.0,
                    error_code=error_code,
                    error_message=error_message or f"Crawler exited with code {return_code}",
                    finished_at=now,
                    updated_at=now,
                )
                return

            try:
                self.storage.update_task(task_id, status="archiving", progress=0.9, updated_at=utc_now_iso())
                self.runner.archive_outputs(output_dir=output_dir, raw_dir=raw_dir, max_contents=options.max_contents)
                self.storage.update_task(
                    task_id,
                    status="ready",
                    progress=1.0,
                    finished_at=utc_now_iso(),
                    updated_at=utc_now_iso(),
                )
            except McpAppError as exc:
                self.storage.update_task(
                    task_id,
                    status="failed",
                    progress=1.0,
                    error_code=exc.code,
                    error_message=exc.detail or exc.message,
                    finished_at=utc_now_iso(),
                    updated_at=utc_now_iso(),
                )
        finally:
            release_xhs_profile(lock_owner)

    def _preflight(
        self,
        keywords: list[str],
        output_dir: Path,
        verify_login_remote: bool,
    ) -> tuple[list[dict[str, str]], dict[str, Any], int | None]:
        checks: list[dict[str, str]] = []
        clean_keywords = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
        if clean_keywords:
            checks.append({"name": "keywords", "status": "passed", "message": "Keywords are available"})
        else:
            checks.append({"name": "keywords", "status": "failed", "message": "Dataset keywords are empty"})

        login_status = self.login_manager.get_login_status(
            "xhs",
            verify_remote=verify_login_remote,
            verify_permission=True,
        )
        if login_status.get("status") == "logged_in" and login_status.get("can_collect") is not False:
            checks.append({"name": "xhs_login", "status": "passed", "message": login_status.get("message") or "Logged in"})
        else:
            checks.append(
                {
                    "name": "xhs_login",
                    "status": "failed",
                    "message": login_status.get("message") or "XHS login or permission verification failed",
                }
            )

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            probe = output_dir / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            checks.append({"name": "output_path", "status": "passed", "message": "Output path is writable"})
        except Exception as exc:
            checks.append({"name": "output_path", "status": "failed", "message": f"Output path is not writable: {exc}"})

        cdp_debug_port = None
        try:
            cdp_debug_port = self._cdp_debug_port_from_endpoint()
            cdp_check = self._check_cdp_endpoint(cdp_debug_port)
            if cdp_check:
                checks.append(cdp_check)
        except McpAppError as exc:
            checks.append({"name": "cdp_endpoint", "status": "failed", "message": exc.detail or exc.message})

        return checks, login_status, cdp_debug_port

    def _browser_mode_options(self) -> tuple[bool, bool]:
        mode = (self.storage.config.browser_mode or "").strip().lower()
        if mode in {"cdp", "cdp_existing", "cdp-connect-existing"}:
            return True, True
        if mode in {"cdp_launch", "cdp-launch", "cdp_new", "cdp-new"}:
            return True, False
        return False, False

    def _cdp_debug_port_from_endpoint(self) -> int | None:
        endpoint = (self.storage.config.cdp_endpoint or "").strip()
        if not endpoint:
            return None
        parsed = urlparse(endpoint if "://" in endpoint else f"http://{endpoint}")
        host = parsed.hostname or "localhost"
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise McpAppError(
                ErrorCode.CDP_ENDPOINT_UNSUPPORTED,
                "Only localhost CDP endpoints are supported",
                f"endpoint={endpoint}",
            )
        return parsed.port or 9222

    def _check_cdp_endpoint(self, cdp_debug_port: int | None) -> dict[str, str] | None:
        enable_cdp_mode, cdp_connect_existing = self._browser_mode_options()
        if not enable_cdp_mode or not cdp_connect_existing:
            return None
        port = cdp_debug_port or 9222
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(2)
                result = sock.connect_ex(("127.0.0.1", port))
            if result == 0:
                return {"name": "cdp_endpoint", "status": "passed", "message": f"CDP endpoint 127.0.0.1:{port} is reachable"}
            return {"name": "cdp_endpoint", "status": "failed", "message": f"Cannot connect to CDP endpoint http://127.0.0.1:{port}"}
        except Exception as exc:
            return {"name": "cdp_endpoint", "status": "failed", "message": f"Cannot connect to CDP endpoint http://127.0.0.1:{port}: {exc}"}

    @staticmethod
    def _extract_crawler_error(log_path: Path | None) -> tuple[str, str | None]:
        if not log_path or not log_path.exists():
            return ErrorCode.CRAWLER_FAILED, None
        try:
            lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-120:]
        except Exception:
            return ErrorCode.CRAWLER_FAILED, None
        text = "\n".join(lines)
        if "您当前登录的账号没有权限访问" in text:
            return ErrorCode.XHS_PERMISSION_DENIED, "您当前登录的账号没有权限访问"
        if ("CDP port" in text and "not accessible" in text) or "Cannot connect to CDP" in text:
            return ErrorCode.CDP_UNAVAILABLE, "Cannot connect to CDP endpoint"
        if "CAPTCHA" in text or "461" in text or "471" in text:
            return ErrorCode.XHS_RISK_CONTROL, "XHS risk control or CAPTCHA appeared"
        if "login" in text.lower() and "fail" in text.lower():
            return ErrorCode.LOGIN_FAILED, "XHS login failed"
        if "timeout" in text.lower() or "timed out" in text.lower():
            return ErrorCode.NETWORK_TIMEOUT, "Network timeout while running crawler"
        return ErrorCode.CRAWLER_FAILED, None
