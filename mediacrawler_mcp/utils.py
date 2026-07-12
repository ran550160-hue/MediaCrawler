from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path


# Query-parameter names that must never appear in report evidence URLs.
# These carry session/auth tokens or signatures that are sensitive.
SENSITIVE_URL_QUERY_PARAMS = frozenset({
    "xsec_token",
    "token",
    "cookie",
    "verifyFp",
    "msToken",
    "a_bogus",
    "signature",
})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def timestamp_for_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return slug[:48] or "dataset"


def make_dataset_id(name: str, datasets_dir: Path) -> str:
    base = f"ds_{timestamp_for_id()}_{slugify(name)}"
    candidate = base
    while (datasets_dir / candidate).exists():
        candidate = f"{base}_{secrets.token_hex(2)}"
    return candidate


def make_report_id(report_type: str) -> str:
    return f"report_{timestamp_for_id()}_{slugify(report_type)}"


def make_task_id(task_type: str) -> str:
    return f"task_{slugify(task_type)}_{timestamp_for_id()}_{secrets.token_hex(2)}"


def setup_file_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if any(isinstance(handler, logging.FileHandler) and handler.baseFilename == str(log_path) for handler in root.handlers):
        return

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)


def strip_sensitive_url_params(url: str | None) -> str | None:
    """Remove sensitive query parameters (xsec_token, token, cookie, etc.) from a URL.

    Preserves the scheme, host, path, and non-sensitive query parameters.  If the
    URL is empty or not http/https, returns it unchanged (callers may separately
    validate scheme).  Does not modify the raw stored URL; only sanitizes report
    output.

    >>> strip_sensitive_url_params("https://www.xiaohongshu.com/explore/abc?xsec_token=AB123&xsec_source=pc_search")
    'https://www.xiaohongshu.com/explore/abc?xsec_source=pc_search'
    >>> strip_sensitive_url_params("https://www.douyin.com/video/123")
    'https://www.douyin.com/video/123'
    >>> strip_sensitive_url_params(None)
    None
    """

    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    if not url:
        return url
    url = str(url).strip()
    parsed = urlparse(url)

    # Only sanitize http/https URLs; leave other schemes (e.g. javascript:) for
    # callers to reject separately.
    if parsed.scheme.lower() not in {"http", "https"}:
        return url

    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in SENSITIVE_URL_QUERY_PARAMS
    ]
    new_query = urlencode(kept)
    sanitized = urlunparse(parsed._replace(query=new_query))
    return sanitized
