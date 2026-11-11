from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.access import get_current_user
from app.core.account_name_policy import (
    AccountNamePolicyError,
    ValidatedAccountNames,
    account_username_identity_key,
    validate_new_account_names,
)
from app.core.config import get_settings
from app.core.login_throttle import register_failure, register_success, seconds_until_unlock
from app.core.security import create_access_token, hash_password, password_needs_rehash, verify_password
from app.database import get_db
from app.models import User
from app.schemas import ChangePassword, CreateUserForm, LoginForm, ResetPassword
from app.services.capabilities import grant_initial_platform_capabilities
from app.services.legacy_demo_a_scope import grant_legacy_demo_a_access
from app.services.metric_definitions import ensure_ops_v1_definitions
from app.services.property_scope import require_platform_admin, resolve_user_scope, scope_summary
from app.services.building_operating_gate import (
    decorate_scope_summary_with_operating_gates,
    resolve_building_operating_gates,
)


router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def _validate_password(password: str) -> None:
    minimum_length = get_settings().min_password_length
    if len(password) < minimum_length:
        raise HTTPException(400, f"密码至少 {minimum_length} 位，且需包含字母和数字")
    if not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password):
        raise HTTPException(400, "密码需同时包含字母和数字")


def _validated_new_account_names(data: LoginForm) -> ValidatedAccountNames:
    try:
        return validate_new_account_names(data.username, data.display_name)
    except AccountNamePolicyError as exc:
        if exc.rule_id and exc.policy_version:
            logger.warning(
                "account_name_rejected field=%s rule_id=%s policy_version=%s",
                exc.field,
                exc.rule_id,
                exc.policy_version,
            )
        raise HTTPException(422, exc.public_message) from None


def _account_username_exists(db: Session, username: str) -> bool:
    identity_key = account_username_identity_key(username)
    return any(
        account_username_identity_key(existing_username) == identity_key
        for (existing_username,) in db.query(User.username).all()
    )


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
    names = _validated_new_account_names(data)
    if _account_username_exists(db, names.username):
        raise HTTPException(400, "用户名已存在")
    _validate_password(data.password)

    user = User(
        username=names.username,
        password_hash=hash_password(data.password),
        display_name=names.display_name,
        role="admin",
    )
    db.add(user)
    try:
        db.flush()
        grant_legacy_demo_a_access(db, user)
        grant_initial_platform_capabilities(db, user)
        ensure_ops_v1_definitions(db, publisher=user)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"message": "注册成功（管理员）", "role": "admin"}


@router.post("/login")
def login(data: LoginForm, request: Request, response: Response, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    lock_seconds = seconds_until_unlock(data.username, client_ip)
    if lock_seconds > 0:
        raise HTTPException(429, f"登录失败次数过多，请 {max(lock_seconds // 60, 1)} 分钟后重试")

    user = db.query(User).filter(User.username == data.username).first()
    if not user or not verify_password(data.password, user.password_hash):
        register_failure(data.username, client_ip)
        raise HTTPException(401, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(403, "用户已停用")

    register_success(data.username, client_ip)
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(data.password)
        db.commit()
        db.refresh(user)
    _set_auth_cookie(response, user)
    return {"message": "登录成功", "username": user.username, "display_name": user.display_name, "role": user.role}


@router.post("/logout")
def logout(response: Response):
    settings = get_settings()
    response.delete_cookie("token", httponly=True, secure=settings.cookie_secure, samesite=settings.cookie_samesite)
    return {"message": "已退出"}


def mark_private_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


@router.get("/me", dependencies=[Depends(mark_private_no_store)])
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    resolved = resolve_user_scope(db, user)
    gates = resolve_building_operating_gates(db, resolved.view_building_ids)
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "scope": decorate_scope_summary_with_operating_gates(
            scope_summary(resolved),
            gates,
        ),
    }


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
def list_users(_admin: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
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
def create_user(data: CreateUserForm, _admin: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
    names = _validated_new_account_names(data)
    if _account_username_exists(db, names.username):
        raise HTTPException(400, "用户名已存在")
    _validate_password(data.password)

    role = data.role or "editor"
    if role not in {"admin", "editor", "viewer"}:
        role = "editor"

    user = User(
        username=names.username,
        password_hash=hash_password(data.password),
        display_name=names.display_name,
        role=role,
    )
    db.add(user)
    try:
        db.flush()
        if data.initial_scope == "legacy_demo_a":
            grant_legacy_demo_a_access(db, user, granted_by=_admin.id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"message": "用户创建成功", "username": user.username}


@router.post("/reset-password")
def reset_password(data: ResetPassword, _admin: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == data.user_id).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    _validate_password(data.new_password)
    user.password_hash = hash_password(data.new_password)
    user.session_version = (user.session_version or 0) + 1
    db.commit()
    return {"message": f"已重置 {user.display_name} 的密码"}
