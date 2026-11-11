from __future__ import annotations

import threading
from contextlib import contextmanager

# 维护模式：备份恢复期间拒绝业务请求，防止恢复窗口内的并发写
# 被覆盖或与 SQLite backup API 抢锁。单进程部署下进程内标志即可。
_maintenance = threading.Event()
MAINTENANCE_MESSAGE = "系统正在恢复备份（维护模式），请稍后重试"


def in_maintenance() -> bool:
    return _maintenance.is_set()


@contextmanager
def maintenance_mode():
    _maintenance.set()
    try:
        yield
    finally:
        _maintenance.clear()
