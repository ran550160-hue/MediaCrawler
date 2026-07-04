from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ErrorCode:
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    UNSUPPORTED_PLATFORM = "UNSUPPORTED_PLATFORM"
    DATASET_NOT_FOUND = "DATASET_NOT_FOUND"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    REPORT_NOT_FOUND = "REPORT_NOT_FOUND"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    RESOURCE_BUSY = "RESOURCE_BUSY"
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
    TASK_TYPE_MISMATCH = "TASK_TYPE_MISMATCH"
    XHS_PERMISSION_DENIED = "XHS_PERMISSION_DENIED"
    REMOTE_VERIFY_FAILED = "REMOTE_VERIFY_FAILED"
    COOKIE_OBSERVED_BUT_INVALID = "COOKIE_OBSERVED_BUT_INVALID"
    CDP_UNAVAILABLE = "CDP_UNAVAILABLE"
    CDP_ENDPOINT_UNSUPPORTED = "CDP_ENDPOINT_UNSUPPORTED"
    LOGIN_FAILED = "LOGIN_FAILED"
    XHS_RISK_CONTROL = "XHS_RISK_CONTROL"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    CRAWLER_FAILED = "CRAWLER_FAILED"
    NORMALIZE_FAILED = "NORMALIZE_FAILED"
    QUERY_FAILED = "QUERY_FAILED"
    REPORT_FAILED = "REPORT_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(slots=True)
class McpAppError(Exception):
    code: str
    message: str
    detail: str | None = None
    payload: dict[str, Any] | None = None

    def to_result(self) -> dict[str, Any]:
        return error_result(self.code, self.message, self.detail, **(self.payload or {}))


def success_result(**payload: Any) -> dict[str, Any]:
    return {"status": "success", **payload}


def error_result(code: str, message: str, detail: str | None = None, **payload: Any) -> dict[str, Any]:
    status = "need_login" if code == ErrorCode.LOGIN_REQUIRED else "failed"
    return {
        "status": status,
        "message": message,
        "error": {
            "code": code,
            "detail": detail or message,
        },
        **payload,
    }
