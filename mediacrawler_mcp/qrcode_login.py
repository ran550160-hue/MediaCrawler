from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from mediacrawler_mcp.crawler_runner import REPO_ROOT
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.login_manager import LoginManager
from mediacrawler_mcp.locks import acquire_xhs_profile, current_xhs_profile_owner, release_xhs_profile
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.utils import make_task_id, utc_now_iso


TERMINAL_STATUSES = {"success", "failed", "cancelled", "expired"}


class QRCodeLoginManager:
    def __init__(self, storage: Storage, repo_root: Path = REPO_ROOT):
        self.storage = storage
        self.repo_root = Path(repo_root)

    def start_qrcode_login(
        self,
        platform: str = "xhs",
        account_name: str = "default",
        timeout_seconds: int = 120,
        headless: bool = True,
        qr_wait_seconds: int = 30,
    ) -> dict[str, Any]:
        platform = self._normalize_platform(platform)
        account_name = (account_name or "default").strip() or "default"
        timeout_seconds = max(30, min(int(timeout_seconds or 120), 300))
        qr_wait_seconds = max(1, min(int(qr_wait_seconds or 30), timeout_seconds))

        self.storage.initialize()
        login_task_id = make_task_id("qrcode_login_xhs")
        lock_owner = self._lock_owner(login_task_id)
        if not acquire_xhs_profile(self.storage.config, lock_owner):
            raise McpAppError(
                ErrorCode.RESOURCE_BUSY,
                "XHS browser profile is busy",
                f"Current owner: {current_xhs_profile_owner(self.storage.config)}",
            )
        release_xhs_profile(lock_owner)

        qr_image_path = self.storage.config.login_qrcodes_dir / f"{login_task_id}.png"
        profile_dir = self.repo_root / "browser_data" / "xhs_user_data_dir"
        worker_log_path = self.storage.config.logs_dir / f"{login_task_id}.log"
        expires_at = (
            datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=timeout_seconds)
        ).isoformat()
        now = utc_now_iso()
        self.storage.upsert_login_session(
            login_session_id=login_task_id,
            platform=platform,
            account_name=account_name,
            status="initializing",
            qr_image_path=str(qr_image_path),
            profile_dir=str(profile_dir),
            expires_at=expires_at,
            message="Starting XHS QR login browser",
            created_at=now,
            updated_at=now,
            worker_log_path=str(worker_log_path),
        )

        process = self._start_worker_process(
            login_task_id=login_task_id,
            account_name=account_name,
            qr_image_path=qr_image_path,
            profile_dir=profile_dir,
            worker_log_path=worker_log_path,
            expires_at=expires_at,
            timeout_seconds=timeout_seconds,
            headless=headless,
        )
        current = self.storage.get_login_session_row(login_task_id) or {}
        self._update_session(
            login_task_id,
            status=current.get("status") or "initializing",
            message=current.get("message") or "Started XHS QR login worker",
            expires_at=expires_at,
            qr_image_path=current.get("qr_image_path") or str(qr_image_path),
            profile_dir=current.get("profile_dir") or str(profile_dir),
            account_name=account_name,
            pid=process.pid,
            worker_log_path=str(worker_log_path),
        )

        deadline = time.time() + qr_wait_seconds
        row = self.storage.get_login_session_row(login_task_id)
        while time.time() < deadline:
            row = self.storage.get_login_session_row(login_task_id)
            if row and row["status"] != "initializing":
                break
            time.sleep(0.2)
        return self._row_to_result(row or self.storage.get_login_session_row(login_task_id))

    def get_qrcode_login_status(self, login_task_id: str) -> dict[str, Any]:
        login_task_id = (login_task_id or "").strip()
        if not login_task_id:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Login task ID is required")
        self.storage.initialize()
        row = self.storage.get_login_session_row(login_task_id)
        if row is None:
            raise McpAppError(ErrorCode.TASK_NOT_FOUND, "Login task not found", f"login_task_id={login_task_id}")
        row = self._repair_waiting_session(row)
        return self._row_to_result(row)

    def cancel_qrcode_login(self, login_task_id: str) -> dict[str, Any]:
        login_task_id = (login_task_id or "").strip()
        if not login_task_id:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Login task ID is required")
        self.storage.initialize()
        row = self.storage.get_login_session_row(login_task_id)
        if row is None:
            raise McpAppError(ErrorCode.TASK_NOT_FOUND, "Login task not found", f"login_task_id={login_task_id}")
        if row["status"] in TERMINAL_STATUSES:
            return self._row_to_result(row)

        now = utc_now_iso()
        self.storage.upsert_login_session(
            login_session_id=login_task_id,
            platform=row["platform"],
            account_name=row["account_name"] or "default",
            status="cancelled",
            qr_image_path=row["qr_image_path"],
            profile_dir=row["profile_dir"],
            expires_at=row["expires_at"],
            message="QR login cancelled",
            created_at=row["created_at"],
            updated_at=now,
            pid=row.get("pid"),
            worker_log_path=row.get("worker_log_path"),
        )
        self._terminate_worker(row.get("pid"))
        return self._row_to_result(self.storage.get_login_session_row(login_task_id))

    def _start_worker_process(
        self,
        login_task_id: str,
        account_name: str,
        qr_image_path: Path,
        profile_dir: Path,
        worker_log_path: Path,
        expires_at: str,
        timeout_seconds: int,
        headless: bool,
    ) -> subprocess.Popen:
        worker_log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["MEDIACRAWLER_MCP_HOME"] = str(self.storage.config.home)
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(self.repo_root), env.get("PYTHONPATH", "")) if part
        )
        command = [
            sys.executable,
            "-m",
            "mediacrawler_mcp.qrcode_worker",
            "--login-task-id",
            login_task_id,
            "--account-name",
            account_name,
            "--qr-image-path",
            str(qr_image_path),
            "--profile-dir",
            str(profile_dir),
            "--expires-at",
            expires_at,
            "--timeout-seconds",
            str(timeout_seconds),
            "--headless",
            str(headless).lower(),
            "--repo-root",
            str(self.repo_root),
        ]
        log_file = worker_log_path.open("ab")
        try:
            process = subprocess.Popen(
                command,
                cwd=self.repo_root,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            log_file.close()
            return process
        except Exception:
            log_file.close()
            raise

    def run_qrcode_worker(
        self,
        login_task_id: str,
        account_name: str,
        qr_image_path: Path,
        profile_dir: Path,
        expires_at: str,
        timeout_seconds: int,
        headless: bool,
    ) -> None:
        self.storage.initialize()
        lock_owner = self._lock_owner(login_task_id)
        if not acquire_xhs_profile(self.storage.config, lock_owner):
            self._update_session(
                login_task_id,
                status="failed",
                message="XHS browser profile is busy",
                expires_at=expires_at,
                qr_image_path=str(qr_image_path),
                profile_dir=str(profile_dir),
                account_name=account_name,
                error_code=ErrorCode.RESOURCE_BUSY,
                error_message=f"Current owner: {current_xhs_profile_owner(self.storage.config)}",
            )
            return
        try:
            asyncio.run(
                self._run_qrcode_login(
                    login_task_id=login_task_id,
                    account_name=account_name,
                    qr_image_path=qr_image_path,
                    profile_dir=profile_dir,
                    expires_at=expires_at,
                    timeout_seconds=timeout_seconds,
                    headless=headless,
                )
            )
        except Exception as exc:
            self._update_session(
                login_task_id,
                status="failed",
                message=f"QR login failed: {exc}",
                expires_at=expires_at,
                qr_image_path=str(qr_image_path),
                profile_dir=str(profile_dir),
                account_name=account_name,
                error_code=ErrorCode.INTERNAL_ERROR,
                error_message=str(exc),
            )
        finally:
            release_xhs_profile(lock_owner)

    async def _run_qrcode_login(
        self,
        login_task_id: str,
        account_name: str,
        qr_image_path: Path,
        profile_dir: Path,
        expires_at: str,
        timeout_seconds: int,
        headless: bool,
    ) -> None:
        self.storage.initialize()
        qr_image_path.parent.mkdir(parents=True, exist_ok=True)
        profile_dir.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
            try:
                await page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded")
                initial_cookie = self._cookie_string(await context.cookies())
                if LoginManager.extract_web_session(initial_cookie):
                    if self._verify_cookie_remote(initial_cookie):
                        self._save_success_cookie(
                            login_task_id,
                            account_name,
                            initial_cookie,
                            profile_dir,
                            expires_at,
                            qr_image_path,
                        )
                        return
                    await self._clear_stale_browser_state(context, page)
                    self._update_session(
                        login_task_id,
                        status="initializing",
                        message="Existing XHS profile cookie failed remote verification. Cleared stale browser state and waiting for QR code.",
                        expires_at=expires_at,
                        qr_image_path=str(qr_image_path),
                        profile_dir=str(profile_dir),
                        account_name=account_name,
                    )

                if not await self._prepare_qr_code(page, qr_image_path, login_task_id):
                    self._update_session(
                        login_task_id,
                        status="failed",
                        message=await self._qr_failure_message(page, login_task_id),
                        expires_at=expires_at,
                        qr_image_path=str(qr_image_path),
                        profile_dir=str(profile_dir),
                        account_name=account_name,
                    )
                    return
                self._update_session(
                    login_task_id,
                    status="waiting_scan",
                    message="QR code is ready. Send qr_image_path to the user.",
                    expires_at=expires_at,
                    qr_image_path=str(qr_image_path),
                    profile_dir=str(profile_dir),
                    account_name=account_name,
                )

                deadline = time.time() + timeout_seconds
                while time.time() < deadline:
                    if self._is_cancelled(login_task_id):
                        self._update_session(
                            login_task_id,
                            status="cancelled",
                            message="QR login cancelled",
                            expires_at=expires_at,
                            qr_image_path=str(qr_image_path),
                            profile_dir=str(profile_dir),
                            account_name=account_name,
                        )
                        return
                    cookie_string = self._cookie_string(await context.cookies())
                    if LoginManager.extract_web_session(cookie_string):
                        if self._verify_cookie_remote(cookie_string):
                            self._save_success_cookie(
                                login_task_id,
                                account_name,
                                cookie_string,
                                profile_dir,
                                expires_at,
                                qr_image_path,
                            )
                            return
                        await self._clear_stale_browser_state(context, page)
                        if not await self._prepare_qr_code(page, qr_image_path, login_task_id):
                            self._update_session(
                                login_task_id,
                                status="failed",
                                message=await self._qr_failure_message(page, login_task_id),
                                expires_at=expires_at,
                                qr_image_path=str(qr_image_path),
                                profile_dir=str(profile_dir),
                                account_name=account_name,
                            )
                            return
                        self._update_session(
                            login_task_id,
                            status="waiting_scan",
                            message="Observed XHS cookie failed remote verification. Refreshed QR code and waiting for a valid login.",
                            expires_at=expires_at,
                            qr_image_path=str(qr_image_path),
                            profile_dir=str(profile_dir),
                            account_name=account_name,
                        )
                    await asyncio.sleep(1)

                self._update_session(
                    login_task_id,
                    status="expired",
                    message="QR login expired",
                    expires_at=expires_at,
                    qr_image_path=str(qr_image_path),
                    profile_dir=str(profile_dir),
                    account_name=account_name,
                )
            finally:
                await context.close()

    async def _prepare_qr_code(self, page: Any, qr_image_path: Path, login_task_id: str) -> bool:
        qr_element = await self._find_qr_element(page, timeout_ms=5000, click_login=False)
        if qr_element is not None:
            await qr_element.screenshot(path=str(qr_image_path))
            return True

        try:
            await page.reload(wait_until="domcontentloaded")
        except Exception:
            pass

        qr_element = await self._find_qr_element(page, timeout_ms=5000, click_login=True)
        if qr_element is not None:
            await qr_element.screenshot(path=str(qr_image_path))
            return True

        await self._save_failure_screenshot(page, login_task_id)
        return False

    async def _find_qr_element(self, page: Any, timeout_ms: int, click_login: bool) -> Any | None:
        selector = "xpath=//img[@class='qrcode-img']"
        try:
            return await page.wait_for_selector(selector, timeout=timeout_ms)
        except PlaywrightTimeoutError:
            pass
        if not click_login:
            return None
        try:
            login_button = page.locator("xpath=//*[@id='app']/div[1]/div[2]/div[1]/ul/div[1]/button")
            await login_button.click(timeout=5000)
            return await page.wait_for_selector(selector, timeout=10000)
        except PlaywrightTimeoutError:
            return None
        except Exception:
            return None

    async def _save_failure_screenshot(self, page: Any, login_task_id: str) -> Path | None:
        screenshot_path = self.storage.config.login_qrcodes_dir / f"{login_task_id}_error.png"
        try:
            await page.screenshot(path=str(screenshot_path), full_page=True)
            return screenshot_path
        except Exception:
            return None

    async def _qr_failure_message(self, page: Any, login_task_id: str) -> str:
        screenshot_path = self.storage.config.login_qrcodes_dir / f"{login_task_id}_error.png"
        try:
            current_url = page.url
        except Exception:
            current_url = "unknown"
        return (
            "Failed to locate XHS QR code after clearing stale browser state and retrying login fallback. "
            f"current_url={current_url}; screenshot_path={screenshot_path}"
        )

    async def _clear_stale_browser_state(self, context: Any, page: Any) -> None:
        try:
            await context.clear_cookies()
        except Exception:
            pass
        try:
            await page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
        except Exception:
            pass

    def _verify_cookie_remote(self, cookie_string: str) -> bool:
        return LoginManager(self.storage, repo_root=self.repo_root)._verify_xhs_cookie_remote(cookie_string)

    def _is_cancelled(self, login_task_id: str) -> bool:
        row = self.storage.get_login_session_row(login_task_id)
        return bool(row and row.get("status") == "cancelled")

    def _save_success_cookie(
        self,
        login_task_id: str,
        account_name: str,
        cookie_string: str,
        profile_dir: Path,
        expires_at: str,
        qr_image_path: Path,
    ) -> None:
        LoginManager(self.storage, repo_root=self.repo_root).import_cookies("xhs", cookie_string, account_name)
        self._update_session(
            login_task_id,
            status="success",
            message="XHS QR login succeeded",
            expires_at=expires_at,
            qr_image_path=str(qr_image_path),
            profile_dir=str(profile_dir),
            account_name=account_name,
        )

    def _repair_waiting_session(self, row: dict[str, Any]) -> dict[str, Any]:
        if row.get("status") != "waiting_scan":
            return row
        if self._worker_alive(row.get("pid")):
            return row
        account_name = row.get("account_name") or "default"
        login_status = LoginManager(self.storage, repo_root=self.repo_root).get_login_status(
            "xhs",
            account_name=account_name,
            verify_remote=True,
        )
        if login_status.get("status") == "logged_in":
            self._update_session(
                row["login_session_id"],
                status="success",
                message="XHS QR login succeeded; status repaired from verified account cookie.",
                expires_at=row.get("expires_at"),
                qr_image_path=row.get("qr_image_path"),
                profile_dir=row.get("profile_dir"),
                account_name=account_name,
                pid=row.get("pid"),
                worker_log_path=row.get("worker_log_path"),
            )
            return self.storage.get_login_session_row(row["login_session_id"]) or row
        if login_status.get("status") == "unknown":
            self._update_session(
                row["login_session_id"],
                status="waiting_scan",
                message="Browser profile detected but not remotely verified. Keep waiting or import a fresh cookie.",
                expires_at=row.get("expires_at"),
                qr_image_path=row.get("qr_image_path"),
                profile_dir=row.get("profile_dir"),
                account_name=account_name,
                pid=row.get("pid"),
                worker_log_path=row.get("worker_log_path"),
            )
            return self.storage.get_login_session_row(row["login_session_id"]) or row
        return row

    def _update_session(
        self,
        login_task_id: str,
        status: str,
        message: str,
        expires_at: str | None,
        qr_image_path: str | None,
        profile_dir: str | None,
        account_name: str,
        pid: int | None = None,
        worker_log_path: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        existing = self.storage.get_login_session_row(login_task_id)
        now = utc_now_iso()
        self.storage.upsert_login_session(
            login_session_id=login_task_id,
            platform="xhs",
            account_name=account_name,
            status=status,
            qr_image_path=qr_image_path,
            profile_dir=profile_dir,
            expires_at=expires_at,
            message=message,
            created_at=(existing or {}).get("created_at") or now,
            updated_at=now,
            pid=pid,
            worker_log_path=worker_log_path,
            error_code=error_code,
            error_message=error_message,
        )

    @staticmethod
    def _worker_alive(pid: Any) -> bool:
        if not pid:
            return False
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False
        except Exception:
            return False

    @staticmethod
    def _terminate_worker(pid: Any) -> None:
        if not pid:
            return
        try:
            if os.name == "nt":
                os.kill(int(pid), signal.SIGTERM)
            else:
                os.kill(int(pid), signal.SIGTERM)
        except OSError:
            return
        except Exception:
            return

    @staticmethod
    def _cookie_string(cookies: list[dict[str, Any]]) -> str:
        return ";".join(f"{cookie.get('name')}={cookie.get('value')}" for cookie in cookies if cookie.get("name"))

    @staticmethod
    def _lock_owner(login_task_id: str) -> str:
        return f"qrcode_login:{login_task_id}"

    @staticmethod
    def _normalize_platform(platform: str) -> str:
        normalized = (platform or "").strip().lower()
        if normalized != "xhs":
            raise McpAppError(ErrorCode.UNSUPPORTED_PLATFORM, "Only xhs QR login is supported")
        return normalized

    @staticmethod
    def _row_to_result(row: dict[str, Any] | None) -> dict[str, Any]:
        if not row:
            return {
                "status": "failed",
                "message": "QR login session was not created",
                "error": {"code": ErrorCode.INTERNAL_ERROR, "detail": "missing login session row"},
            }
        qr_image_path = row["qr_image_path"]
        qr_image_exists = bool(qr_image_path and Path(qr_image_path).exists())
        qr_ready = row["status"] == "waiting_scan" and qr_image_exists
        qr_image_size = Path(qr_image_path).stat().st_size if qr_image_exists and qr_image_path else None
        worker_pid = row.get("pid")
        return {
            "status": row["status"],
            "login_task_id": row["login_session_id"],
            "platform": row["platform"],
            "account_name": row["account_name"],
            "qr_image_path": qr_image_path,
            "qr_ready": qr_ready,
            "qr_image_exists": qr_image_exists,
            "qr_image_mime": "image/png" if qr_image_path else None,
            "qr_image_size_bytes": qr_image_size,
            "suggested_message": "请在二维码过期前扫码登录小红书" if qr_ready else None,
            "expires_in_seconds": QRCodeLoginManager._expires_in_seconds(row.get("expires_at")),
            "profile_dir": row["profile_dir"],
            "worker_pid": worker_pid,
            "worker_alive": QRCodeLoginManager._worker_alive(worker_pid),
            "worker_log_path": row.get("worker_log_path"),
            "error_code": row.get("error_code"),
            "error_message": row.get("error_message"),
            "expires_at": row["expires_at"],
            "message": row["message"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _expires_in_seconds(expires_at: str | None) -> int | None:
        if not expires_at:
            return None
        try:
            expires = datetime.fromisoformat(expires_at)
            return max(0, int((expires - datetime.now(timezone.utc)).total_seconds()))
        except Exception:
            return None
