from __future__ import annotations

import threading
import time

# 登录防爆破：账号维度 5 次连续失败锁 15 分钟；
# IP 维度阈值放宽到 20 次，避免办公网共享出口被个别账号锁死。
USER_MAX_FAILURES = 5
IP_MAX_FAILURES = 20
LOCKOUT_SECONDS = 15 * 60
WINDOW_SECONDS = 15 * 60
_MAX_TRACKED_KEYS = 5000

_lock = threading.Lock()
_failures: dict[str, list[float]] = {}


def _prune(now: float) -> None:
    if len(_failures) <= _MAX_TRACKED_KEYS:
        return
    cutoff = now - max(LOCKOUT_SECONDS, WINDOW_SECONDS)
    for key in [key for key, stamps in _failures.items() if not stamps or stamps[-1] < cutoff]:
        _failures.pop(key, None)


def _recent_failures(key: str, now: float) -> list[float]:
    stamps = _failures.get(key, [])
    fresh = [stamp for stamp in stamps if now - stamp < WINDOW_SECONDS]
    if fresh:
        _failures[key] = fresh
    else:
        _failures.pop(key, None)
    return fresh


def seconds_until_unlock(username: str, client_ip: str) -> int:
    """返回剩余锁定秒数；0 表示未锁定。"""
    now = time.time()
    with _lock:
        remaining = 0
        for key, limit in ((f"user:{username}", USER_MAX_FAILURES), (f"ip:{client_ip}", IP_MAX_FAILURES)):
            stamps = _recent_failures(key, now)
            if len(stamps) >= limit:
                unlock_at = stamps[-1] + LOCKOUT_SECONDS
                remaining = max(remaining, int(unlock_at - now) + 1)
        return max(remaining, 0)


def register_failure(username: str, client_ip: str) -> None:
    now = time.time()
    with _lock:
        for key in (f"user:{username}", f"ip:{client_ip}"):
            stamps = _recent_failures(key, now)
            stamps.append(now)
            _failures[key] = stamps
        _prune(now)


def register_success(username: str, client_ip: str) -> None:
    with _lock:
        _failures.pop(f"user:{username}", None)
        _failures.pop(f"ip:{client_ip}", None)


def reset_login_throttle() -> None:
    with _lock:
        _failures.clear()
