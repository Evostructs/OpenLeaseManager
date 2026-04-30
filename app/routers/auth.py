from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.access import get_current_user, require_admin
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.database import get_db
from app.models import User
from app.schemas import ChangePassword, LoginForm, ResetPassword


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _validate_password(password: str) -> None:
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位，且需包含字母和数字")
    if not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password):
        raise HTTPException(400, "密码需同时包含字母和数字")


def _set_auth_cookie(response: Response, user: User) -> None:
    token = create_access_token(
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_version=user.session_version or 0,
    )
    settings = get_settings()
    response.set_cookie(
        "token",
        token,
        max_age=settings.token_expire_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )


@router.get("/status")
def auth_status(db: Session = Depends(get_db)):
    user_count = db.query(User).count()
    return {"need_init": user_count == 0, "user_count": user_count}


@router.post("/register")
def register(data: LoginForm, db: Session = Depends(get_db)):
    if db.query(User).count() > 0:
        raise HTTPException(403, "系统已初始化，请联系管理员创建账号")
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(400, "用户名已存在")
    _validate_password(data.password)

    user = User(
        username=data.username,
        password_hash=hash_password(data.password),
        display_name=data.display_name or data.username,
        role="admin",
    )
    db.add(user)
    db.commit()
    return {"message": "注册成功（管理员）", "role": "admin"}


@router.post("/login")
def login(data: LoginForm, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == data.username).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(403, "用户已停用")

    _set_auth_cookie(response, user)
    return {"message": "登录成功", "username": user.username, "display_name": user.display_name, "role": user.role}


@router.post("/logout")
def logout(response: Response):
    settings = get_settings()
    response.delete_cookie("token", httponly=True, secure=settings.cookie_secure, samesite=settings.cookie_samesite)
    return {"message": "已退出"}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role}


@router.post("/change-password")
def change_password(
    data: ChangePassword,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(data.old_password, user.password_hash):
        raise HTTPException(400, "原密码错误")
    _validate_password(data.new_password)
    user.password_hash = hash_password(data.new_password)
    user.session_version = (user.session_version or 0) + 1
    db.commit()
    db.refresh(user)
    _set_auth_cookie(response, user)
    return {"message": "密码修改成功"}


@router.get("/users")
def list_users(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [
        {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "created_at": user.created_at,
        }
        for user in users
    ]


@router.post("/create-user")
def create_user(data: LoginForm, _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(400, "用户名已存在")
    _validate_password(data.password)

    role = data.role or "editor"
    if role not in {"admin", "editor", "viewer"}:
        role = "editor"

    user = User(
        username=data.username,
        password_hash=hash_password(data.password),
        display_name=data.display_name or data.username,
        role=role,
    )
    db.add(user)
    db.commit()
    return {"message": "用户创建成功", "username": user.username}


@router.post("/reset-password")
def reset_password(data: ResetPassword, _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == data.user_id).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    _validate_password(data.new_password)
    user.password_hash = hash_password(data.new_password)
    user.session_version = (user.session_version or 0) + 1
    db.commit()
    return {"message": f"已重置 {user.display_name} 的密码"}
