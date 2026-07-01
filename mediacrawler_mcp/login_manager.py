from __future__ import annotations

from pathlib import Path
from typing import Any

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

    def get_login_status(self, platform: str = SUPPORTED_PLATFORM, account_name: str = DEFAULT_ACCOUNT_NAME) -> dict[str, Any]:
        platform = self._normalize_platform(platform)
        account_name = self._normalize_account_name(account_name)
        self.storage.initialize()

        now = utc_now_iso()
        cookie_path = self.cookie_file_path(platform, account_name)
        cookie_string = self._read_cookie_file(cookie_path)
        if cookie_string and self.extract_web_session(cookie_string):
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
                "message": "XHS cookie with web_session is available. Live remote validation is not performed in this sprint.",
            }

        profile_dir = self._find_available_profile_dir(platform, account_name)
        if profile_dir:
            self._upsert_account(
                platform=platform,
                account_name=account_name,
                profile_dir=str(profile_dir),
                status="logged_in",
                last_login_at=None,
                last_checked_at=now,
            )
            return {
                "status": "logged_in",
                "platform": platform,
                "account_name": account_name,
                "login_source": "browser_profile",
                "cookie_file_path": str(cookie_path) if cookie_path.exists() else None,
                "profile_dir": str(profile_dir),
                "last_checked_at": now,
                "message": "Detected a local XHS browser profile. Live remote validation is not performed in this sprint.",
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
        cookie_string = self._read_cookie_file(self.cookie_file_path(platform, account_name))
        if cookie_string and self.extract_web_session(cookie_string):
            return cookie_string
        return None

    def cookie_file_path(self, platform: str = SUPPORTED_PLATFORM, account_name: str = DEFAULT_ACCOUNT_NAME) -> Path:
        return self.storage.config.accounts_dir / platform / account_name / "cookies.txt"

    def manual_login_message(self) -> str:
        return (
            "XHS login is required. For local testing, run a manual QR login once with "
            "`uv run python main.py --platform xhs --lt qrcode --type search --keywords \"test\" "
            "--crawler_max_notes_count 1 --get_comment false --headless false`, or call import_cookies "
            "with a cookie string containing web_session."
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

    @staticmethod
    def extract_web_session(cookie_string: str) -> str | None:
        for part in (cookie_string or "").split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            if key.strip() == "web_session" and value.strip():
                return value.strip()
        return None
