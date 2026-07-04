from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from mediacrawler_mcp.crawler_runner import REPO_ROOT
from mediacrawler_mcp.errors import ErrorCode, McpAppError
from mediacrawler_mcp.storage import Storage
from mediacrawler_mcp.utils import utc_now_iso


SUPPORTED_PLATFORM = "xhs"
DEFAULT_ACCOUNT_NAME = "default"


class LoginManager:
    def __init__(self, storage: Storage, repo_root: Path = REPO_ROOT):
        self.storage = storage
        self.repo_root = Path(repo_root)

    def get_login_status(
        self,
        platform: str = SUPPORTED_PLATFORM,
        account_name: str = DEFAULT_ACCOUNT_NAME,
        verify_remote: bool = False,
        verify_permission: bool = False,
    ) -> dict[str, Any]:
        platform = self._normalize_platform(platform)
        account_name = self._normalize_account_name(account_name)
        self.storage.initialize()

        now = utc_now_iso()
        cookie_path = self.cookie_file_path(platform, account_name)
        cookie_string = self._read_cookie_file(cookie_path)
        if cookie_string and self.extract_web_session(cookie_string):
            account_row = self.storage.get_account_row(f"{platform}:{account_name}")
            if not verify_remote and account_row and account_row.get("status") == "expired":
                return {
                    "status": "expired",
                    "platform": platform,
                    "account_name": account_name,
                    "login_source": "cookie",
                    "cookie_file_path": str(cookie_path),
                    "profile_dir": str(cookie_path.parent),
                    "last_checked_at": account_row.get("last_checked_at"),
                    "remote_verified": False,
                    "permission_verified": False,
                    "can_collect": False,
                    "error_code": "COOKIE_EXPIRED",
                    "message": "XHS cookie was previously marked expired. Re-import a fresh cookie.",
                }
            if verify_remote and not self._verify_xhs_cookie_remote(cookie_string):
                self._upsert_account(
                    platform=platform,
                    account_name=account_name,
                    profile_dir=str(cookie_path.parent),
                    status="expired",
                    last_login_at=None,
                    last_checked_at=now,
                )
                return {
                    "status": "expired",
                    "platform": platform,
                    "account_name": account_name,
                    "login_source": "cookie",
                    "cookie_file_path": str(cookie_path),
                    "profile_dir": str(cookie_path.parent),
                    "last_checked_at": now,
                    "remote_verified": False,
                    "permission_verified": False,
                    "can_collect": False,
                    "error_code": "COOKIE_EXPIRED",
                    "message": "XHS cookie exists but remote selfinfo verification failed. Re-import a fresh cookie.",
                }
            permission = {"status": "skipped", "can_collect": None, "error_code": None, "message": None}
            if verify_permission:
                permission = self._verify_xhs_collect_permission(cookie_string)
                if not permission["can_collect"]:
                    status = "permission_denied" if permission["error_code"] == "XHS_PERMISSION_DENIED" else "limited"
                    self._upsert_account(
                        platform=platform,
                        account_name=account_name,
                        profile_dir=str(cookie_path.parent),
                        status=status,
                        last_login_at=None,
                        last_checked_at=now,
                    )
                    return {
                        "status": status,
                        "platform": platform,
                        "account_name": account_name,
                        "login_source": "cookie",
                        "cookie_file_path": str(cookie_path),
                        "profile_dir": str(cookie_path.parent),
                        "last_checked_at": now,
                        "remote_verified": verify_remote,
                        "permission_verified": True,
                        "can_collect": False,
                        "error_code": permission["error_code"],
                        "message": permission["message"],
                    }
            self._upsert_account(
                platform=platform,
                account_name=account_name,
                profile_dir=str(cookie_path.parent),
                status="logged_in",
                last_login_at=None,
                last_checked_at=now,
            )
            return {
                "status": "logged_in",
                "platform": platform,
                "account_name": account_name,
                "login_source": "cookie",
                "cookie_file_path": str(cookie_path),
                "profile_dir": str(cookie_path.parent),
                "last_checked_at": now,
                "remote_verified": verify_remote,
                "permission_verified": verify_permission,
                "can_collect": permission["can_collect"] if verify_permission else None,
                "error_code": None,
                "message": (
                    "XHS cookie remote and permission verification succeeded."
                    if verify_remote and verify_permission
                    else "XHS cookie remote verification succeeded."
                    if verify_remote
                    else "XHS cookie with web_session is available. Remote validation was not requested."
                ),
            }

        profile_dir = self._find_available_profile_dir(platform, account_name)
        if profile_dir:
            self._upsert_account(
                platform=platform,
                account_name=account_name,
                profile_dir=str(profile_dir),
                status="unknown",
                last_login_at=None,
                last_checked_at=now,
            )
            return {
                "status": "unknown",
                "platform": platform,
                "account_name": account_name,
                "login_source": "browser_profile",
                "cookie_file_path": str(cookie_path) if cookie_path.exists() else None,
                "profile_dir": str(profile_dir),
                "last_checked_at": now,
                "remote_verified": False,
                "permission_verified": False,
                "can_collect": None,
                "error_code": "PROFILE_NOT_VERIFIED",
                "message": "Detected a local XHS browser profile, but no remotely verified cookie is available.",
            }

        self._upsert_account(
            platform=platform,
            account_name=account_name,
            profile_dir=str(cookie_path.parent),
            status="logged_out",
            last_login_at=None,
            last_checked_at=now,
        )
        return {
            "status": "logged_out",
            "platform": platform,
            "account_name": account_name,
            "login_source": None,
            "cookie_file_path": str(cookie_path) if cookie_path.exists() else None,
            "profile_dir": None,
            "last_checked_at": now,
            "remote_verified": False,
            "permission_verified": False,
            "can_collect": False,
            "error_code": "LOGIN_REQUIRED",
            "message": self.manual_login_message(),
        }

    def import_cookies(
        self,
        platform: str,
        cookie_string: str,
        account_name: str = DEFAULT_ACCOUNT_NAME,
    ) -> dict[str, Any]:
        platform = self._normalize_platform(platform)
        account_name = self._normalize_account_name(account_name)
        cookie_string = (cookie_string or "").strip()
        if not cookie_string:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Cookie string is required")
        if not self.extract_web_session(cookie_string):
            raise McpAppError(
                ErrorCode.INVALID_ARGUMENT,
                "XHS cookie must contain web_session",
                "MediaCrawler's XHS cookie login currently relies on the web_session cookie.",
            )

        self.storage.initialize()
        cookie_path = self.cookie_file_path(platform, account_name)
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        cookie_path.write_text(cookie_string + "\n", encoding="utf-8")

        now = utc_now_iso()
        self._upsert_account(
            platform=platform,
            account_name=account_name,
            profile_dir=str(cookie_path.parent),
            status="logged_in",
            last_login_at=now,
            last_checked_at=now,
        )
        return {
            "status": "success",
            "platform": platform,
            "account_name": account_name,
            "cookie_file_path": str(cookie_path),
            "message": "XHS cookie imported. Cookie value is stored locally and is never returned by MCP tools.",
        }

    def get_cookie_string(self, platform: str = SUPPORTED_PLATFORM, account_name: str = DEFAULT_ACCOUNT_NAME) -> str | None:
        platform = self._normalize_platform(platform)
        account_name = self._normalize_account_name(account_name)
        account_row = self.storage.get_account_row(f"{platform}:{account_name}")
        if account_row and account_row.get("status") in {"expired", "permission_denied", "limited"}:
            return None
        cookie_string = self._read_cookie_file(self.cookie_file_path(platform, account_name))
        if cookie_string and self.extract_web_session(cookie_string):
            return cookie_string
        return None

    def cookie_file_path(self, platform: str = SUPPORTED_PLATFORM, account_name: str = DEFAULT_ACCOUNT_NAME) -> Path:
        return self.storage.config.accounts_dir / platform / account_name / "cookies.txt"

    def manual_login_message(self) -> str:
        return (
            "XHS login is required. For local testing, run a manual QR login once from the MediaCrawler "
            "repo root: `python main.py --platform xhs --lt qrcode --type search --keywords \"test\" "
            "--crawler_max_notes_count 1 --get_comment false --headless false`. If the server uses a venv, "
            "activate it first, for example `. .venv/bin/activate`. You can also call import_cookies with "
            "a cookie string containing web_session."
        )

    def _normalize_platform(self, platform: str) -> str:
        normalized = (platform or "").strip().lower()
        if not normalized:
            raise McpAppError(ErrorCode.INVALID_ARGUMENT, "Platform is required")
        if normalized != SUPPORTED_PLATFORM:
            raise McpAppError(ErrorCode.UNSUPPORTED_PLATFORM, "Only xhs login is supported")
        return normalized

    def _normalize_account_name(self, account_name: str) -> str:
        normalized = (account_name or DEFAULT_ACCOUNT_NAME).strip()
        return normalized or DEFAULT_ACCOUNT_NAME

    def _known_profile_dirs(self, platform: str, account_name: str) -> list[Path]:
        if platform != SUPPORTED_PLATFORM:
            return []
        return [
            self.repo_root / "browser_data" / "xhs_user_data_dir",
            self.repo_root / "browser_data" / "cdp_xhs_user_data_dir",
            self.storage.config.home / "browser_profiles" / platform / account_name,
        ]

    def _find_available_profile_dir(self, platform: str, account_name: str) -> Path | None:
        for profile_dir in self._known_profile_dirs(platform, account_name):
            if not profile_dir.exists() or not profile_dir.is_dir():
                continue
            try:
                if any(profile_dir.iterdir()):
                    return profile_dir
            except OSError:
                continue
        return None

    def _read_cookie_file(self, cookie_path: Path) -> str | None:
        if not cookie_path.exists():
            return None
        try:
            cookie_string = cookie_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return cookie_string or None

    def _upsert_account(
        self,
        platform: str,
        account_name: str,
        profile_dir: str,
        status: str,
        last_login_at: str | None,
        last_checked_at: str | None,
    ) -> None:
        account_id = f"{platform}:{account_name}"
        existing = self.storage.get_account_row(account_id)
        now = utc_now_iso()
        self.storage.upsert_account(
            account_id=account_id,
            platform=platform,
            account_name=account_name,
            profile_dir=profile_dir,
            status=status,
            last_login_at=last_login_at or (existing or {}).get("last_login_at"),
            last_checked_at=last_checked_at,
            created_at=(existing or {}).get("created_at") or now,
            updated_at=now,
        )

    def _verify_xhs_cookie_remote(self, cookie_string: str) -> bool:
        uri = "/api/sns/web/v1/user/selfinfo"
        host = "https://edith.xiaohongshu.com"
        try:
            from media_platform.xhs.playwright_sign import sign_with_xhshow

            signs = sign_with_xhshow(uri=uri, data={}, cookie_str=cookie_string, method="GET")
            headers = {
                "accept": "application/json, text/plain, */*",
                "accept-language": "zh-CN,zh;q=0.9",
                "cache-control": "no-cache",
                "content-type": "application/json;charset=UTF-8",
                "origin": "https://www.xiaohongshu.com",
                "pragma": "no-cache",
                "referer": "https://www.xiaohongshu.com/",
                "user-agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
                ),
                "Cookie": cookie_string,
                "X-S": signs["x-s"],
                "X-T": signs["x-t"],
                "x-S-Common": signs["x-s-common"],
                "X-B3-Traceid": signs["x-b3-traceid"],
            }
            response = httpx.get(f"{host}{uri}", headers=headers, timeout=15, trust_env=False)
            if response.status_code != 200:
                return False
            payload = response.json()
            return payload.get("success") is True
        except Exception:
            return False

    def _verify_xhs_collect_permission(self, cookie_string: str) -> dict[str, Any]:
        uri = "/api/sns/web/v1/search/notes"
        host = "https://edith.xiaohongshu.com"
        data = {
            "keyword": "test",
            "page": 1,
            "page_size": 1,
            "search_id": "mcp_preflight",
            "sort": "general",
            "note_type": 0,
        }
        try:
            import json

            from media_platform.xhs.playwright_sign import sign_with_xhshow

            signs = sign_with_xhshow(uri=uri, data=data, cookie_str=cookie_string, method="POST")
            headers = {
                "accept": "application/json, text/plain, */*",
                "accept-language": "zh-CN,zh;q=0.9",
                "cache-control": "no-cache",
                "content-type": "application/json;charset=UTF-8",
                "origin": "https://www.xiaohongshu.com",
                "pragma": "no-cache",
                "referer": "https://www.xiaohongshu.com/",
                "user-agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
                ),
                "Cookie": cookie_string,
                "X-S": signs["x-s"],
                "X-T": signs["x-t"],
                "x-S-Common": signs["x-s-common"],
                "X-B3-Traceid": signs["x-b3-traceid"],
            }
            response = httpx.post(
                f"{host}{uri}",
                headers=headers,
                data=json.dumps(data, separators=(",", ":"), ensure_ascii=False),
                timeout=15,
                trust_env=False,
            )
            if response.status_code in (461, 471):
                return {
                    "status": "failed",
                    "can_collect": False,
                    "error_code": "XHS_RISK_CONTROL",
                    "message": "XHS risk control or CAPTCHA appeared during permission preflight.",
                }
            payload = response.json()
            if payload.get("success") is True:
                return {"status": "success", "can_collect": True, "error_code": None, "message": None}
            message = payload.get("msg") or response.text
            if "权限" in message or "permission" in message.lower():
                return {
                    "status": "failed",
                    "can_collect": False,
                    "error_code": "XHS_PERMISSION_DENIED",
                    "message": message,
                }
            return {"status": "failed", "can_collect": False, "error_code": "XHS_LIMITED", "message": message}
        except Exception as exc:
            return {"status": "failed", "can_collect": False, "error_code": "UNKNOWN", "message": str(exc)}

    @staticmethod
    def extract_web_session(cookie_string: str) -> str | None:
        for part in (cookie_string or "").split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            if key.strip() == "web_session" and value.strip():
                return value.strip()
        return None
