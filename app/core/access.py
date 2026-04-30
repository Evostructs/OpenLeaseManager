from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database import get_db
from app.models import User


def _extract_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    bearer = header.removeprefix("Bearer ").strip()
    return request.cookies.get("token") or bearer


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except Exception as exc:  # pragma: no cover - defensive path
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录") from exc

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")

    token_session_version = int(payload.get("session_version", 0))
    if token_session_version != (user.session_version or 0):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录")
    return user


def require_role(*allowed_roles: str) -> Callable[[User], User]:
    def _dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权执行此操作")
        return user

    return _dependency


def require_editor(user: User = Depends(get_current_user)) -> User:
    return require_role("admin", "editor")(user)


def require_admin(user: User = Depends(get_current_user)) -> User:
    return require_role("admin")(user)
