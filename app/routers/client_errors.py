from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.common import log_action

# 前端运行时错误上报：免认证（登录页也可能出错），按来源 IP 滑动窗口限流防刷。
router = APIRouter(prefix="/api/audit", tags=["client-errors"])

_REPORT_WINDOW_SECONDS = 60
_REPORT_MAX_PER_WINDOW = 10
_report_bucket: dict[str, list[float]] = {}


class ClientErrorReport(BaseModel):
    message: str = Field(default="", max_length=300)
    source: str = Field(default="", max_length=200)
    detail: str = Field(default="", max_length=500)


def _allow_report(ip: str) -> bool:
    now = time.time()
    bucket = [t for t in _report_bucket.get(ip, []) if now - t < _REPORT_WINDOW_SECONDS]
    if len(bucket) >= _REPORT_MAX_PER_WINDOW:
        _report_bucket[ip] = bucket
        return False
    bucket.append(now)
    _report_bucket[ip] = bucket
    return True


@router.post("/client-error")
def report_client_error(data: ClientErrorReport, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    if not _allow_report(ip):
        return {"ok": False}
    message = data.message.strip() or "unknown error"
    source = data.source.strip()
    detail = data.detail.strip()
    changes = message if not source else f"{message} @ {source}"
    if detail:
        changes = f"{changes}\n{detail}"
    log_action(db, None, "client_error", changes=changes, operator="system", target_type="frontend")
    return {"ok": True}
