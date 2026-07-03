from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Any

from mediacrawler_mcp.config import McpConfig
from mediacrawler_mcp.utils import utc_now_iso


_HANDLE_GUARD = threading.Lock()
_LOCK_HANDLES: dict[str, Any] = {}
_LOCK_METADATA: dict[str, dict[str, Any]] = {}


def acquire_xhs_profile(config: McpConfig, owner: str) -> bool:
    owner = (owner or "").strip()
    if not owner:
        return False

    lock_path = _xhs_profile_lock_path(config)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        _try_lock(handle)
    except OSError:
        handle.close()
        return False

    metadata = {
        "owner": owner,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "purpose": owner.split(":", 1)[0],
        "created_at": utc_now_iso(),
        "lock_path": str(lock_path),
    }
    handle.seek(0)
    handle.truncate()
    json.dump(metadata, handle, ensure_ascii=False)
    handle.flush()
    os.fsync(handle.fileno())

    with _HANDLE_GUARD:
        _LOCK_HANDLES[owner] = handle
        _LOCK_METADATA[owner] = metadata
    return True


def release_xhs_profile(owner: str) -> None:
    owner = (owner or "").strip()
    with _HANDLE_GUARD:
        handle = _LOCK_HANDLES.pop(owner, None)
        _LOCK_METADATA.pop(owner, None)
    if handle is None:
        return

    try:
        handle.seek(0)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        try:
            _unlock(handle)
        finally:
            handle.close()


def current_xhs_profile_owner(config: McpConfig) -> str | None:
    metadata = current_xhs_profile_metadata(config)
    owner = metadata.get("owner")
    return str(owner) if owner else None


def current_xhs_profile_metadata(config: McpConfig) -> dict[str, Any]:
    lock_path = _xhs_profile_lock_path(config)
    with _HANDLE_GUARD:
        for metadata in _LOCK_METADATA.values():
            if metadata.get("lock_path") == str(lock_path):
                return dict(metadata)

    try:
        payload = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    if not payload:
        return {}
    try:
        metadata = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _xhs_profile_lock_path(config: McpConfig) -> Path:
    return config.locks_dir / "xhs_profile.lock"


def _try_lock(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
