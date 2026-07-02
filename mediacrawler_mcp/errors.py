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

    def to_result(self) -> dict[str, Any]:
        return error_result(self.code, self.message, self.detail)


def success_result(**payload: Any) -> dict[str, Any]:
    return {"status": "success", **payload}


def error_result(code: str, message: str, detail: str | None = None) -> dict[str, Any]:
    status = "need_login" if code == ErrorCode.LOGIN_REQUIRED else "failed"
    return {
        "status": status,
        "message": message,
        "error": {
            "code": code,
            "detail": detail or message,
        },
    }
