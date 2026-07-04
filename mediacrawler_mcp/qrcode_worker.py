from __future__ import annotations

import argparse
from pathlib import Path

from mediacrawler_mcp.config import load_config
from mediacrawler_mcp.qrcode_login import QRCodeLoginManager
from mediacrawler_mcp.storage import Storage


def _to_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def main() -> None:
    parser = argparse.ArgumentParser(description="MediaCrawler MCP QR login worker")
    parser.add_argument("--login-task-id", required=True)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--qr-image-path", required=True)
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--timeout-seconds", type=int, required=True)
    parser.add_argument("--headless", default="true")
    parser.add_argument("--repo-root", required=True)
    args = parser.parse_args()

    storage = Storage(load_config())
    manager = QRCodeLoginManager(storage, repo_root=Path(args.repo_root))
    manager.run_qrcode_worker(
        login_task_id=args.login_task_id,
        account_name=args.account_name,
        qr_image_path=Path(args.qr_image_path),
        profile_dir=Path(args.profile_dir),
        expires_at=args.expires_at,
        timeout_seconds=args.timeout_seconds,
        headless=_to_bool(args.headless),
    )


if __name__ == "__main__":
    main()
