from __future__ import annotations

import asyncio
import json
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


TERMINAL_STATUSES = {
    "success",
    "failed",
    "cancelled",
    "expired",
    "permission_denied",
    "remote_verify_failed",
    "cookie_observed_but_invalid",
}

XHS_DESKTOP_MAC_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)

XHS_DESKTOP_MAC_FINGERPRINT_SCRIPT = """
(() => {
  const defineGetter = (target, name, getter) => {
    try {
      Object.defineProperty(target, name, {
        get: getter,
        configurable: true,
      });
    } catch (_) {}
  };

  defineGetter(Navigator.prototype, 'platform', () => 'MacIntel');
  defineGetter(Navigator.prototype, 'languages', () => ['zh-CN', 'zh', 'en']);
  defineGetter(Navigator.prototype, 'webdriver', () => undefined);

  if (navigator.userAgentData) {
    const brands = [
      { brand: 'Google Chrome', version: '137' },
      { brand: 'Chromium', version: '137' },
      { brand: 'Not.A/Brand', version: '99' },
    ];
    const fullVersionList = [
      { brand: 'Google Chrome', version: '137.0.0.0' },
      { brand: 'Chromium', version: '137.0.0.0' },
      { brand: 'Not.A/Brand', version: '99.0.0.0' },
    ];
    const highEntropyValues = {
      architecture: 'x86',
      bitness: '64',
      brands,
      fullVersionList,
      mobile: false,
      model: '',
      platform: 'macOS',
      platformVersion: '14.0.0',
      uaFullVersion: '137.0.0.0',
      wow64: false,
    };
    const userAgentData = {
      brands,
      mobile: false,
      platform: 'macOS',
      getHighEntropyValues: async (hints = []) => {
        const values = {
          brands,
          mobile: false,
          platform: 'macOS',
        };
        for (const hint of hints) {
          if (Object.prototype.hasOwnProperty.call(highEntropyValues, hint)) {
            values[hint] = highEntropyValues[hint];
          }
        }
        return values;
      },
      toJSON: () => ({
        brands,
        mobile: false,
        platform: 'macOS',
      }),
    };
    defineGetter(Navigator.prototype, 'userAgentData', () => userAgentData);
  }
})();
"""


class QRCodeLoginManager:
    QR_CONFIRM_GRACE_SECONDS: float = 25
    QR_POLL_INTERVAL_SECONDS: float = 1
    REMOTE_VERIFY_WINDOW_SECONDS: float = 30
    REMOTE_VERIFY_INTERVAL_SECONDS: float = 3
    XHS_COOKIE_SETTLE_SECONDS: float = 5
    XHS_COOKIE_SETTLE_SAMPLE_SECONDS: float = 0.5
    XHS_REQUIRED_SETTLED_COOKIE_NAMES = frozenset({"web_session", "id_token"})
    XHS_EXPECTED_SETTLED_COOKIE_NAMES = frozenset(
        {
            "web_session",
            "id_token",
            "x-rednote-datactry",
            "x-rednote-holderctry",
            "acw_tc",
            "websectiga",
            "sec_poison_id",
        }
    )

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

        qr_image_path = self._qr_image_path(login_task_id, 1)
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
        self._log_worker("worker started", login_task_id=login_task_id, account_name=account_name)
        lock_owner = self._lock_owner(login_task_id)
        if not acquire_xhs_profile(self.storage.config, lock_owner):
            self._log_worker(
                "worker failed to acquire profile lock",
                login_task_id=login_task_id,
                owner=current_xhs_profile_owner(self.storage.config),
            )
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
            self._log_worker("worker exception", login_task_id=login_task_id, error=str(exc))
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
            self._log_worker("worker exited", login_task_id=login_task_id)

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
        current_qr_image_path = qr_image_path
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                viewport={"width": 1920, "height": 1080},
                user_agent=XHS_DESKTOP_MAC_USER_AGENT,
            )
            self._log_worker("browser launched", login_task_id=login_task_id, profile_dir=str(profile_dir))
            await self._add_stealth_script(context, login_task_id)
            await self._add_xhs_desktop_mac_fingerprint(context, login_task_id)
            page = await context.new_page()
            try:
                await page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded")
                self._log_worker("page opened", login_task_id=login_task_id, url=self._safe_page_url(page))
                initial_cookies = await context.cookies()
                initial_cookie = self._cookie_string(initial_cookies)
                if LoginManager.extract_web_session(initial_cookie):
                    self._log_worker(
                        "web_session observed",
                        login_task_id=login_task_id,
                        source="initial_profile",
                        cookie_names=self._cookie_names(initial_cookies),
                    )
                    initial_verify = self._normalize_verify_result(self._verify_cookie_remote(initial_cookie))
                    self._log_verify_attempt(
                        login_task_id,
                        attempt=1,
                        result=initial_verify,
                        source="initial_profile",
                    )
                    if initial_verify.get("ok"):
                        self._save_success_cookie(
                            login_task_id,
                            account_name,
                            initial_cookie,
                            profile_dir,
                            expires_at,
                            qr_image_path,
                        )
                        return
                    self._log_worker(
                        "stale initial login cookie cleared",
                        login_task_id=login_task_id,
                        error_code=initial_verify.get("error_code"),
                        message=initial_verify.get("message"),
                    )
                    await self._clear_stale_login_cookie(context, page, login_task_id)
                    self._update_session(
                        login_task_id,
                        status="initializing",
                        message="Existing XHS profile cookie failed remote verification. Cleared stale login cookie while preserving browser device state.",
                        expires_at=expires_at,
                        qr_image_path=str(qr_image_path),
                        profile_dir=str(profile_dir),
                        account_name=account_name,
                    )

                current_qr_image_path = self._qr_image_path(login_task_id, 1)
                if not await self._prepare_qr_code(page, current_qr_image_path, login_task_id, qr_index=1):
                    self._update_session(
                        login_task_id,
                        status="failed",
                        message=await self._qr_failure_message(page, login_task_id),
                        expires_at=expires_at,
                        qr_image_path=str(current_qr_image_path),
                        profile_dir=str(profile_dir),
                        account_name=account_name,
                    )
                    return
                self._update_session(
                    login_task_id,
                    status="waiting_scan",
                    message="QR code is ready. Send qr_image_path to the user.",
                    expires_at=expires_at,
                    qr_image_path=str(current_qr_image_path),
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
                            qr_image_path=str(current_qr_image_path),
                            profile_dir=str(profile_dir),
                            account_name=account_name,
                        )
                        self._log_worker("cancelled", login_task_id=login_task_id)
                        return
                    cookies = await context.cookies()
                    cookie_string = self._cookie_string(cookies)
                    if LoginManager.extract_web_session(cookie_string):
                        self._log_worker(
                            "web_session observed",
                            login_task_id=login_task_id,
                            cookie_names=self._cookie_names(cookies),
                        )
                        await self._verify_observed_cookie_until_terminal(
                            context=context,
                            login_task_id=login_task_id,
                            account_name=account_name,
                            profile_dir=profile_dir,
                            expires_at=expires_at,
                            qr_image_path=current_qr_image_path,
                            timeout_deadline=deadline,
                        )
                        return
                    await asyncio.sleep(max(0.1, float(self.QR_POLL_INTERVAL_SECONDS)))

                if timeout_seconds > 0 and self.QR_CONFIRM_GRACE_SECONDS > 0:
                    observed = await self._observe_cookie_after_qr_expiry(
                        context=context,
                        login_task_id=login_task_id,
                        account_name=account_name,
                        profile_dir=profile_dir,
                        expires_at=expires_at,
                        qr_image_path=current_qr_image_path,
                    )
                    if observed:
                        return

                self._update_session(
                    login_task_id,
                    status="expired",
                    message="QR login expired",
                    expires_at=expires_at,
                    qr_image_path=str(current_qr_image_path),
                    profile_dir=str(profile_dir),
                    account_name=account_name,
                )
                self._log_worker("expired", login_task_id=login_task_id)
            finally:
                await context.close()

    async def _observe_cookie_after_qr_expiry(
        self,
        context: Any,
        login_task_id: str,
        account_name: str,
        profile_dir: Path,
        expires_at: str,
        qr_image_path: Path,
    ) -> bool:
        """Watch briefly after QR expiry for delayed cookie writes from a confirmed phone scan."""
        grace_seconds = max(0.0, float(self.QR_CONFIRM_GRACE_SECONDS))
        if grace_seconds <= 0:
            return False
        self._update_session(
            login_task_id,
            status="expired_pending_cookie",
            message="QR timer expired; waiting briefly for delayed cookie write after phone confirmation.",
            expires_at=expires_at,
            qr_image_path=str(qr_image_path),
            profile_dir=str(profile_dir),
            account_name=account_name,
        )
        self._log_worker("expired pending cookie grace", login_task_id=login_task_id, grace_seconds=grace_seconds)
        deadline = time.time() + grace_seconds
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
                self._log_worker("cancelled during expiry grace", login_task_id=login_task_id)
                return True
            cookies = await context.cookies()
            cookie_string = self._cookie_string(cookies)
            if LoginManager.extract_web_session(cookie_string):
                self._log_worker(
                    "web_session observed after expiry",
                    login_task_id=login_task_id,
                    cookie_names=self._cookie_names(cookies),
                )
                await self._verify_observed_cookie_until_terminal(
                    context=context,
                    login_task_id=login_task_id,
                    account_name=account_name,
                    profile_dir=profile_dir,
                    expires_at=expires_at,
                    qr_image_path=qr_image_path,
                    timeout_deadline=time.time() + max(0.1, float(self.REMOTE_VERIFY_WINDOW_SECONDS)),
                )
                return True
            await asyncio.sleep(max(0.1, float(self.QR_POLL_INTERVAL_SECONDS)))
        self._log_worker("expiry grace ended without cookie", login_task_id=login_task_id)
        return False

    async def _prepare_qr_code(self, page: Any, qr_image_path: Path, login_task_id: str, qr_index: int) -> bool:
        qr_element = await self._find_qr_element(page, timeout_ms=5000, click_login=False)
        if qr_element is not None:
            await qr_element.screenshot(path=str(qr_image_path))
            self._log_qr_image("QR generated" if qr_index == 1 else "QR refreshed", login_task_id, qr_image_path, qr_index)
            return True

        try:
            await page.reload(wait_until="domcontentloaded")
            self._log_worker("page reloaded while preparing QR", login_task_id=login_task_id, url=self._safe_page_url(page))
        except Exception:
            pass

        qr_element = await self._find_qr_element(page, timeout_ms=5000, click_login=True)
        if qr_element is not None:
            await qr_element.screenshot(path=str(qr_image_path))
            self._log_qr_image("QR generated" if qr_index == 1 else "QR refreshed", login_task_id, qr_image_path, qr_index)
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

    async def _add_stealth_script(self, context: Any, login_task_id: str) -> None:
        stealth_path = self.repo_root / "libs" / "stealth.min.js"
        if not stealth_path.exists():
            self._log_worker("stealth script missing", login_task_id=login_task_id, path=str(stealth_path))
            return
        try:
            await context.add_init_script(path=str(stealth_path))
            self._log_worker("stealth script added", login_task_id=login_task_id, path=str(stealth_path))
        except Exception as exc:
            self._log_worker("stealth script add failed", login_task_id=login_task_id, error=str(exc))

    async def _add_xhs_desktop_mac_fingerprint(self, context: Any, login_task_id: str) -> None:
        try:
            await context.add_init_script(script=XHS_DESKTOP_MAC_FINGERPRINT_SCRIPT)
            self._log_worker(
                "desktop mac fingerprint script added",
                login_task_id=login_task_id,
                platform="MacIntel",
                languages="zh-CN,zh,en",
                ua_platform="macOS",
            )
        except Exception as exc:
            self._log_worker("desktop mac fingerprint script add failed", login_task_id=login_task_id, error=str(exc))

    async def _clear_stale_login_cookie(self, context: Any, page: Any, login_task_id: str) -> None:
        """Drop only the invalid login cookie; keep XHS browser device identifiers intact."""
        try:
            await context.clear_cookies(name="web_session")
            self._log_worker("web_session cookie cleared by context filter", login_task_id=login_task_id)
            return
        except TypeError:
            self._log_worker("context cookie filter unsupported; falling back to page cookie expiry", login_task_id=login_task_id)
        except Exception as exc:
            self._log_worker("context cookie filter failed; falling back to page cookie expiry", login_task_id=login_task_id, error=str(exc))

        try:
            await page.evaluate(
                """() => {
                    const expires = 'Thu, 01 Jan 1970 00:00:00 GMT';
                    const domains = [location.hostname, '.xiaohongshu.com', 'www.xiaohongshu.com'];
                    for (const domain of domains) {
                        document.cookie = `web_session=; expires=${expires}; path=/; domain=${domain}`;
                    }
                    document.cookie = `web_session=; expires=${expires}; path=/`;
                }"""
            )
            self._log_worker("web_session cookie expired by page script", login_task_id=login_task_id)
        except Exception as exc:
            self._log_worker("web_session cookie page expiry failed", login_task_id=login_task_id, error=str(exc))

    async def _verify_observed_cookie_until_terminal(
        self,
        context: Any,
        login_task_id: str,
        account_name: str,
        profile_dir: Path,
        expires_at: str,
        qr_image_path: Path,
        timeout_deadline: float,
    ) -> None:
        observed_at = utc_now_iso()
        self._update_session(
            login_task_id,
            status="cookie_observed",
            message="Detected XHS login cookie after QR scan. Remote verification is in progress.",
            expires_at=expires_at,
            qr_image_path=str(qr_image_path),
            profile_dir=str(profile_dir),
            account_name=account_name,
            verification_attempts=0,
            observed_cookie_at=observed_at,
        )
        verify_deadline = min(
            timeout_deadline,
            time.time() + max(0.1, float(self.REMOTE_VERIFY_WINDOW_SECONDS)),
        )
        observed_monotonic = time.time()
        attempts = 0
        last_result: dict[str, Any] | None = None
        last_cookie_metadata: dict[str, Any] | None = None
        while time.time() < verify_deadline:
            if self._is_cancelled(login_task_id):
                self._update_session(
                    login_task_id,
                    status="cancelled",
                    message="QR login cancelled",
                    expires_at=expires_at,
                    qr_image_path=str(qr_image_path),
                    profile_dir=str(profile_dir),
                    account_name=account_name,
                    verification_attempts=attempts,
                    observed_cookie_at=observed_at,
                )
                self._log_worker("cancelled during remote verify", login_task_id=login_task_id)
                return

            cookies = await self._settled_verification_cookies(
                context=context,
                login_task_id=login_task_id,
                observed_monotonic=observed_monotonic,
                verify_deadline=verify_deadline,
            )
            last_cookie_metadata = self._safe_cookie_metadata(cookies)
            cookie_string = self._cookie_string(cookies)
            attempts += 1
            if not LoginManager.extract_web_session(cookie_string):
                last_result = {
                    "ok": False,
                    "status": "cookie_observed_but_invalid",
                    "error_code": ErrorCode.COOKIE_OBSERVED_BUT_INVALID,
                    "message": "web_session cookie disappeared before remote verification succeeded.",
                    "http_status": None,
                    "xhs_code": None,
                    "xhs_msg": None,
                }
            else:
                last_result = self._normalize_verify_result(self._verify_cookie_remote(cookie_string))
            self._log_verify_attempt(
                login_task_id,
                attempts,
                last_result,
                source="qr_scan",
                cookie_metadata=last_cookie_metadata,
            )

            if last_result.get("ok"):
                self._save_success_cookie(
                    login_task_id,
                    account_name,
                    cookie_string,
                    profile_dir,
                    expires_at,
                    qr_image_path,
                    verification_attempts=attempts,
                    observed_cookie_at=observed_at,
                )
                return

            self._update_session(
                login_task_id,
                status="cookie_observed",
                message="Detected XHS login cookie after QR scan. Remote verification is still retrying.",
                expires_at=expires_at,
                qr_image_path=str(qr_image_path),
                profile_dir=str(profile_dir),
                account_name=account_name,
                verification_attempts=attempts,
                last_verify_error_code=last_result.get("error_code"),
                last_verify_message=last_result.get("message"),
                observed_cookie_at=observed_at,
            )
            self._log_worker(
                "cookie kept for retry",
                login_task_id=login_task_id,
                attempt=attempts,
                error_code=last_result.get("error_code"),
            )
            if self._should_fast_fail_verify(last_result, last_cookie_metadata):
                self._log_worker(
                    "remote verify fast fail",
                    login_task_id=login_task_id,
                    attempt=attempts,
                    error_code=last_result.get("error_code"),
                    xhs_code=last_result.get("xhs_code"),
                    message=last_result.get("message"),
                    missing_expected_cookie_names=(last_cookie_metadata or {}).get("missing_expected_cookie_names"),
                )
                break
            await asyncio.sleep(max(0.1, float(self.REMOTE_VERIFY_INTERVAL_SECONDS)))

        terminal_status, error_code = self._terminal_status_for_verify_result(last_result)
        message = (last_result or {}).get("message") or "Observed XHS cookie did not pass remote verification."
        self._update_session(
            login_task_id,
            status=terminal_status,
            message=message,
            expires_at=expires_at,
            qr_image_path=str(qr_image_path),
            profile_dir=str(profile_dir),
            account_name=account_name,
            error_code=error_code,
            error_message=message,
            verification_attempts=attempts,
            last_verify_error_code=(last_result or {}).get("error_code"),
            last_verify_message=message,
            observed_cookie_at=observed_at,
        )
        self._log_worker(
            "terminal failure",
            login_task_id=login_task_id,
            status=terminal_status,
            attempts=attempts,
            error_code=error_code,
            message=message,
            missing_expected_cookie_names=(last_cookie_metadata or {}).get("missing_expected_cookie_names"),
        )

    async def _settled_verification_cookies(
        self,
        context: Any,
        login_task_id: str,
        observed_monotonic: float,
        verify_deadline: float,
    ) -> list[dict[str, Any]]:
        """Let browser-written login cookies settle before making signed API verification calls."""
        settle_until = min(
            verify_deadline,
            observed_monotonic + max(0.0, float(self.XHS_COOKIE_SETTLE_SECONDS)),
        )
        sample_interval = max(0.05, float(self.XHS_COOKIE_SETTLE_SAMPLE_SECONDS))
        last_cookies: list[dict[str, Any]] = []
        last_names: tuple[str, ...] | None = None
        stable_samples = 0
        logged_wait = False

        while True:
            cookies = await context.cookies()
            last_cookies = list(cookies)
            metadata = self._safe_cookie_metadata(last_cookies)
            names = tuple(metadata["cookie_names"])
            if names == last_names:
                stable_samples += 1
            else:
                stable_samples = 1
                last_names = names

            missing_required = [
                name
                for name in sorted(self.XHS_REQUIRED_SETTLED_COOKIE_NAMES)
                if name in metadata["missing_expected_cookie_names"]
            ]
            missing_expected = metadata["missing_expected_cookie_names"]
            should_wait = (
                time.time() < settle_until
                and (missing_required or missing_expected or stable_samples < 2)
            )
            if not should_wait:
                self._log_worker(
                    "cookie snapshot ready for remote verify",
                    login_task_id=login_task_id,
                    cookie_count=metadata["cookie_count"],
                    cookie_names=metadata["cookie_names"],
                    duplicate_cookie_names=metadata["duplicate_cookie_names"],
                    missing_expected_cookie_names=metadata["missing_expected_cookie_names"],
                    stable_samples=stable_samples,
                )
                return last_cookies

            if not logged_wait:
                self._log_worker(
                    "waiting for settled cookie snapshot",
                    login_task_id=login_task_id,
                    cookie_count=metadata["cookie_count"],
                    cookie_names=metadata["cookie_names"],
                    duplicate_cookie_names=metadata["duplicate_cookie_names"],
                    missing_expected_cookie_names=metadata["missing_expected_cookie_names"],
                )
                logged_wait = True
            await asyncio.sleep(min(sample_interval, max(0.05, settle_until - time.time())))

    def _verify_cookie_remote(self, cookie_string: str) -> dict[str, Any]:
        return LoginManager(self.storage, repo_root=self.repo_root)._verify_xhs_cookie_remote_detail(cookie_string)

    @staticmethod
    def _normalize_verify_result(result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            return result
        if result is True:
            return {
                "ok": True,
                "status": "logged_in",
                "error_code": None,
                "message": "XHS cookie remote verification succeeded.",
                "http_status": None,
                "xhs_code": None,
                "xhs_msg": None,
            }
        return {
            "ok": False,
            "status": "remote_verify_failed",
            "error_code": ErrorCode.REMOTE_VERIFY_FAILED,
            "message": "XHS cookie remote verification failed.",
            "http_status": None,
            "xhs_code": None,
            "xhs_msg": None,
        }

    @staticmethod
    def _terminal_status_for_verify_result(result: dict[str, Any] | None) -> tuple[str, str]:
        if not result:
            return "cookie_observed_but_invalid", ErrorCode.COOKIE_OBSERVED_BUT_INVALID
        error_code = result.get("error_code") or ErrorCode.COOKIE_OBSERVED_BUT_INVALID
        if error_code == ErrorCode.XHS_PERMISSION_DENIED or result.get("status") == "permission_denied":
            return "permission_denied", ErrorCode.XHS_PERMISSION_DENIED
        if error_code == ErrorCode.REMOTE_VERIFY_FAILED:
            return "remote_verify_failed", ErrorCode.REMOTE_VERIFY_FAILED
        return "cookie_observed_but_invalid", ErrorCode.COOKIE_OBSERVED_BUT_INVALID

    @staticmethod
    def _should_fast_fail_verify(
        result: dict[str, Any] | None,
        cookie_metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not result:
            return False
        is_permission_denied = (
            result.get("error_code") == ErrorCode.XHS_PERMISSION_DENIED
            or result.get("xhs_code") == -104
        )
        if not is_permission_denied:
            return False
        missing = set((cookie_metadata or {}).get("missing_expected_cookie_names") or [])
        if missing.intersection(QRCodeLoginManager.XHS_REQUIRED_SETTLED_COOKIE_NAMES):
            return False
        return True

    def _log_verify_attempt(
        self,
        login_task_id: str,
        attempt: int,
        result: dict[str, Any],
        source: str,
        cookie_metadata: dict[str, Any] | None = None,
    ) -> None:
        event = "remote verify passed" if result.get("ok") else "remote verify failed"
        metadata = cookie_metadata or {}
        self._log_worker(
            event,
            login_task_id=login_task_id,
            attempt=attempt,
            source=source,
            status=result.get("status"),
            error_code=result.get("error_code"),
            http_status=result.get("http_status"),
            xhs_code=result.get("xhs_code"),
            xhs_msg=result.get("xhs_msg"),
            message=result.get("message"),
            cookie_count=metadata.get("cookie_count"),
            cookie_names=metadata.get("cookie_names"),
            duplicate_cookie_names=metadata.get("duplicate_cookie_names"),
            missing_expected_cookie_names=metadata.get("missing_expected_cookie_names"),
        )

    def _log_qr_image(self, event: str, login_task_id: str, qr_image_path: Path, qr_index: int) -> None:
        try:
            size = qr_image_path.stat().st_size
        except OSError:
            size = None
        self._log_worker(
            event,
            login_task_id=login_task_id,
            qr_index=qr_index,
            qr_image_path=str(qr_image_path),
            qr_image_size_bytes=size,
        )

    @staticmethod
    def _log_worker(event: str, **fields: Any) -> None:
        safe_fields = {key: value for key, value in fields.items() if value is not None}
        print(f"{utc_now_iso()} {event} {json.dumps(safe_fields, ensure_ascii=False)}", flush=True)

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
        verification_attempts: int | None = None,
        observed_cookie_at: str | None = None,
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
            verification_attempts=verification_attempts,
            last_verify_error_code="",
            last_verify_message="",
            observed_cookie_at=observed_cookie_at,
        )
        self._log_worker(
            "remote verify passed; cookie saved",
            login_task_id=login_task_id,
            verification_attempts=verification_attempts,
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
        verification_attempts: int | None = None,
        last_verify_error_code: str | None = None,
        last_verify_message: str | None = None,
        observed_cookie_at: str | None = None,
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
            verification_attempts=verification_attempts,
            last_verify_error_code=last_verify_error_code,
            last_verify_message=last_verify_message,
            observed_cookie_at=observed_cookie_at,
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
    def _cookie_names(cookies: list[dict[str, Any]]) -> list[str]:
        return sorted(str(cookie.get("name")) for cookie in cookies if cookie.get("name"))

    @classmethod
    def _safe_cookie_metadata(cls, cookies: list[dict[str, Any]]) -> dict[str, Any]:
        names = [str(cookie.get("name")) for cookie in cookies if cookie.get("name")]
        unique_names = sorted(set(names))
        duplicate_names = sorted({name for name in names if names.count(name) > 1})
        missing_expected = sorted(cls.XHS_EXPECTED_SETTLED_COOKIE_NAMES.difference(unique_names))
        return {
            "cookie_count": len(names),
            "cookie_names": unique_names,
            "duplicate_cookie_names": duplicate_names,
            "missing_expected_cookie_names": missing_expected,
        }

    def _qr_image_path(self, login_task_id: str, qr_index: int) -> Path:
        return self.storage.config.login_qrcodes_dir / f"{login_task_id}_qr_{qr_index}.png"

    @staticmethod
    def _safe_page_url(page: Any) -> str:
        try:
            return str(page.url)
        except Exception:
            return "unknown"

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
            "verification_attempts": row.get("verification_attempts") or 0,
            "last_verify_error_code": row.get("last_verify_error_code") or None,
            "last_verify_message": row.get("last_verify_message") or None,
            "observed_cookie_at": row.get("observed_cookie_at"),
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
