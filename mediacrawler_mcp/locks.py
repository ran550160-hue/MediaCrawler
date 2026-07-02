from __future__ import annotations

import threading


_XHS_PROFILE_LOCK = threading.Lock()
_XHS_PROFILE_OWNER: str | None = None
_OWNER_GUARD = threading.Lock()


def acquire_xhs_profile(owner: str) -> bool:
    global _XHS_PROFILE_OWNER
    acquired = _XHS_PROFILE_LOCK.acquire(blocking=False)
    if not acquired:
        return False
    with _OWNER_GUARD:
        _XHS_PROFILE_OWNER = owner
    return True


def release_xhs_profile(owner: str) -> None:
    global _XHS_PROFILE_OWNER
    with _OWNER_GUARD:
        if _XHS_PROFILE_OWNER != owner:
            return
        _XHS_PROFILE_OWNER = None
    _XHS_PROFILE_LOCK.release()


def current_xhs_profile_owner() -> str | None:
    with _OWNER_GUARD:
        return _XHS_PROFILE_OWNER
