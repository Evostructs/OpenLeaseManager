from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, or_, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.time import now_in_timezone, today_in_timezone
from app.models import (
    AuthorizationControl,
    Building,
    Portfolio,
    User,
    UserAuthorizationRevision,
    UserCapability,
    UserPortfolioOverviewGrant,
    UserScopeGrant,
)
from app.services.common import dump_changes, log_action
from app.services.legacy_demo_a_scope import ObjectScope
from app.services.property_scope import resolve_user_scope, scope_summary


ALLOWED_CAPABILITIES = frozenset(
    {
        "platform_admin",
        "customer_master_manage",
        "data_import_submit",
        "snapshot_review",
        "snapshot_publish",
        "metric_definition_manage",
        "snapshot_exchange_receive",
    }
)
EDIT_ROLES = frozenset({"admin", "editor"})


def _active_on(model, target_date):
    return (
        model.valid_from <= target_date,
        or_(model.valid_to.is_(None), model.valid_to >= target_date),
    )


def _stable_code(value: object, *, label: str) -> str:
    raw_code = str(value or "")
    code = raw_code.strip()
    if not code:
        raise HTTPException(422, f"{label}不能为空")
    if raw_code != code:
        raise HTTPException(422, f"{label}不能包含首尾空格")
    if re.fullmatch(r"[+-]?\d+", code):
        raise HTTPException(422, f"{label}必须使用稳定代码，不能使用数字 ID")
    return code


def _target_user(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        raise HTTPException(404, "用户不存在")
    return user


def describe_user_authorization(db: Session, user_id: int) -> dict:
    user = _target_user(db, user_id)
    resolved = resolve_user_scope(db, user)
    today = today_in_timezone()
    grants = (
        db.query(UserScopeGrant)
        .filter(UserScopeGrant.user_id == user.id, *_active_on(UserScopeGrant, today))
        .order_by(UserScopeGrant.id.asc())
        .all()
    )
    overview_grants = (
        db.query(UserPortfolioOverviewGrant)
        .filter(
            UserPortfolioOverviewGrant.user_id == user.id,
            *_active_on(UserPortfolioOverviewGrant, today),
        )
        .order_by(UserPortfolioOverviewGrant.id.asc())
        .all()
    )
    building_codes = {
        int(item.id): str(item.code).strip()
        for item in db.query(Building).filter(
            Building.id.in_([grant.building_id for grant in grants if grant.building_id] or [-1])
        )
    }
    portfolio_codes = {
        int(item.id): str(item.code).strip()
        for item in db.query(Portfolio).filter(
            Portfolio.id.in_(
                [grant.portfolio_id for grant in grants if grant.portfolio_id]
                + [grant.portfolio_id for grant in overview_grants]
                or [-1]
            )
        )
    }
    grant_rows = [
        {
            "scope_type": grant.scope_type,
            "scope_code": (
                building_codes.get(int(grant.building_id))
                if grant.scope_type == "building" and grant.building_id is not None
                else portfolio_codes.get(int(grant.portfolio_id))
                if grant.scope_type == "portfolio" and grant.portfolio_id is not None
                else None
            ),
            "access_level": grant.access_level,
            "include_future_buildings": bool(grant.include_future_buildings),
            "valid_from": grant.valid_from.isoformat() if grant.valid_from else None,
            "valid_to": grant.valid_to.isoformat() if grant.valid_to else None,
            "granted_by": int(grant.granted_by) if grant.granted_by is not None else None,
            "granted_at": grant.granted_at.isoformat() if grant.granted_at else None,
        }
        for grant in grants
    ]
    grant_rows.extend(
        {
            "scope_type": "portfolio_overview",
            "scope_code": portfolio_codes.get(int(grant.portfolio_id)),
            "access_level": "view",
            "include_future_buildings": bool(grant.include_future_buildings),
            "valid_from": grant.valid_from.isoformat() if grant.valid_from else None,
            "valid_to": grant.valid_to.isoformat() if grant.valid_to else None,
            "granted_by": int(grant.granted_by) if grant.granted_by is not None else None,
            "granted_at": grant.granted_at.isoformat() if grant.granted_at else None,
        }
        for grant in overview_grants
    )
    return {
        "user": {
            "id": int(user.id),
            "username": user.username,
            "display_name": user.display_name or "",
            "role": user.role,
            "is_active": bool(user.is_active),
        },
        "scope": scope_summary(resolved),
        "authorization_revision": resolved.authorization_revision,
        "authorization_version": resolved.authorization_version,
        "grants": grant_rows,
        "capabilities": list(resolved.effective_capabilities),
    }


def _resolve_grants(db: Session, user: User, grants: Iterable[dict]) -> list[dict]:
    resolved: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for item in grants:
        scope_type = str(item.get("scope_type") or "").strip().lower()
        if scope_type not in {"building", "portfolio", "portfolio_overview"}:
            raise HTTPException(
                422,
                "授权范围类型必须是 building、portfolio 或 portfolio_overview",
            )
        code = _stable_code(item.get("scope_code"), label="授权范围代码")
        access_level = str(item.get("access_level") or "").strip().lower()
        if access_level not in {"view", "edit"}:
            raise HTTPException(422, "授权级别必须是 view 或 edit")
        if scope_type == "portfolio_overview" and access_level != "view":
            raise HTTPException(422, "板块概况授权只能是 view")
        if access_level == "edit" and str(user.role or "") not in EDIT_ROLES:
            raise HTTPException(422, "viewer 角色不能获得编辑授权")
        include_future = bool(item.get("include_future_buildings", False))
        if scope_type == "building":
            matches = [
                item
                for item in db.query(Building).filter(Building.is_active.is_(True)).all()
                if str(item.code).strip().casefold() == code.casefold()
            ]
            if not matches:
                raise HTTPException(404, "楼宇不存在")
            if len(matches) != 1:
                raise HTTPException(409, "楼宇稳定代码存在冲突，请先修复基础数据")
            target = matches[0]
            if include_future:
                raise HTTPException(422, "单楼授权不能包含未来楼宇")
            key = (scope_type, int(target.id))
            row = {
                "scope_type": scope_type,
                "portfolio_id": None,
                "building_id": int(target.id),
                "scope_code": str(target.code).strip(),
                "access_level": access_level,
                "include_future_buildings": False,
            }
        else:
            matches = [
                item
                for item in db.query(Portfolio).filter(Portfolio.is_active.is_(True)).all()
                if str(item.code).strip().casefold() == code.casefold()
            ]
            if not matches:
                raise HTTPException(404, "板块不存在")
            if len(matches) != 1:
                raise HTTPException(409, "板块稳定代码存在冲突，请先修复基础数据")
            target = matches[0]
            key = (scope_type, int(target.id))
            row = {
                "scope_type": scope_type,
                "portfolio_id": int(target.id),
                "building_id": None,
                "scope_code": str(target.code).strip(),
                "access_level": access_level,
                "include_future_buildings": include_future,
            }
        if key in seen:
            raise HTTPException(422, "同一授权范围不能重复")
        seen.add(key)
        resolved.append(row)
    return resolved


def _resolved_grant_key(item: dict) -> tuple:
    return (
        str(item["scope_type"]),
        int(item["portfolio_id"]) if item["portfolio_id"] is not None else None,
        int(item["building_id"]) if item["building_id"] is not None else None,
        str(item["access_level"]),
        bool(item["include_future_buildings"]),
    )


def _stored_scope_grant_key(item: UserScopeGrant) -> tuple:
    return (
        str(item.scope_type),
        int(item.portfolio_id) if item.portfolio_id is not None else None,
        int(item.building_id) if item.building_id is not None else None,
        str(item.access_level),
        bool(item.include_future_buildings),
    )


def _stored_overview_grant_key(item: UserPortfolioOverviewGrant) -> tuple:
    return (
        "portfolio_overview",
        int(item.portfolio_id),
        None,
        "view",
        bool(item.include_future_buildings),
    )


def _validate_capabilities(values: Iterable[object]) -> tuple[str, ...]:
    requested = tuple(sorted({str(value or "").strip() for value in values if str(value or "").strip()}))
    invalid = sorted(set(requested) - ALLOWED_CAPABILITIES)
    if invalid:
        raise HTTPException(422, f"未知平台能力：{invalid[0]}")
    return requested


def _acquire_authorization_control(db: Session) -> int:
    """Serialize every authorization document write on one durable database row."""
    changed_at = now_in_timezone()
    result = db.execute(
        update(AuthorizationControl)
        .where(AuthorizationControl.id == 1)
        .values(
            global_revision=AuthorizationControl.global_revision + 1,
            updated_at=changed_at,
        ),
        execution_options={"synchronize_session": False},
    )
    if result.rowcount != 1:
        raise RuntimeError("authorization control row is missing")
    db.flush()
    revision = db.query(AuthorizationControl.global_revision).filter(AuthorizationControl.id == 1).scalar()
    if revision is None:
        raise RuntimeError("authorization control row is missing")
    return int(revision)


def _is_concurrent_write_error(exc: OperationalError) -> bool:
    message = str(getattr(exc, "orig", exc)).casefold()
    return any(
        marker in message
        for marker in (
            "database is locked",
            "database table is locked",
            "database is busy",
            "deadlock",
            "could not serialize",
            "serialization failure",
            "lock wait timeout",
        )
    )


def _operator_still_controls_authorization(db: Session, operator_id: int) -> User:
    operator = _target_user(db, operator_id)
    if not operator.is_active:
        raise HTTPException(409, "管理员授权已发生变化，请重新登录后重试")
    today = today_in_timezone()
    has_capability = (
        db.query(UserCapability.id)
        .filter(
            UserCapability.user_id == operator.id,
            UserCapability.capability == "platform_admin",
            *_active_on(UserCapability, today),
        )
        .first()
        is not None
    )
    if not has_capability:
        raise HTTPException(409, "管理员授权已发生变化，请重新登录后重试")
    return operator


def _history_document(db: Session, description: dict) -> dict:
    user_id = int(description["user"]["id"])
    today = today_in_timezone()
    capability_rows = (
        db.query(UserCapability)
        .filter(UserCapability.user_id == user_id, *_active_on(UserCapability, today))
        .order_by(UserCapability.id.asc())
        .all()
    )
    return {
        "authorization_revision": int(description["authorization_revision"]),
        "authorization_version": description["authorization_version"],
        "grants": description["grants"],
        "capabilities": [
            {
                "capability": row.capability,
                "valid_from": row.valid_from.isoformat() if row.valid_from else None,
                "valid_to": row.valid_to.isoformat() if row.valid_to else None,
                "granted_by": int(row.granted_by) if row.granted_by is not None else None,
                "granted_at": row.granted_at.isoformat() if row.granted_at else None,
            }
            for row in capability_rows
        ],
    }


def _ensure_platform_admin_survives(
    db: Session,
    *,
    target_user: User,
    requested_capabilities: tuple[str, ...],
) -> None:
    if "platform_admin" in requested_capabilities:
        return
    today = today_in_timezone()
    target_has_capability = (
        db.query(UserCapability.id)
        .filter(
            UserCapability.user_id == target_user.id,
            UserCapability.capability == "platform_admin",
            *_active_on(UserCapability, today),
        )
        .first()
        is not None
    )
    if not target_has_capability:
        return
    other_admin = (
        db.query(UserCapability.id)
        .join(User, User.id == UserCapability.user_id)
        .filter(
            UserCapability.user_id != target_user.id,
            UserCapability.capability == "platform_admin",
            User.is_active.is_(True),
            *_active_on(UserCapability, today),
        )
        .first()
    )
    if other_admin is None:
        raise HTTPException(409, "不能移除最后一名平台管理员的管理能力")


def replace_user_authorization(
    db: Session,
    user_id: int,
    *,
    grants: Iterable[dict],
    capabilities: Iterable[object],
    expected_authorization_version: str,
    operator: User,
) -> dict:
    if operator.id is None:
        raise ValueError("operator must be persisted")
    operator_id = int(operator.id)
    try:
        # The singleton row is updated before any authorization decision.  It is
        # the cross-user serialization point for last-admin safety, while the
        # per-user revision below is the compare-and-swap guard.
        global_revision = _acquire_authorization_control(db)
        db.expire_all()
        current_operator = _operator_still_controls_authorization(db, operator_id)
        target = _target_user(db, user_id)
        before = describe_user_authorization(db, user_id)
        if str(expected_authorization_version or "") != before["authorization_version"]:
            raise HTTPException(409, "用户授权已被其他管理员修改，请刷新后重试")

        resolved_grants = _resolve_grants(db, target, grants)
        requested_capabilities = _validate_capabilities(capabilities)
        _ensure_platform_admin_survives(
            db,
            target_user=target,
            requested_capabilities=requested_capabilities,
        )
        before_history = _history_document(db, before)
        today = today_in_timezone()
        changed_at = now_in_timezone()

        # Keep an unchanged, open-ended scope grant on its original boundary.
        # Recreating a frozen portfolio grant would move granted_at forward and
        # silently absorb memberships added after the original authorization.
        current_scope_grants = (
            db.query(UserScopeGrant)
            .filter(
                UserScopeGrant.user_id == target.id,
                UserScopeGrant.valid_from <= today,
                UserScopeGrant.valid_to.is_(None),
            )
            .all()
        )
        current_overview_grants = (
            db.query(UserPortfolioOverviewGrant)
            .filter(
                UserPortfolioOverviewGrant.user_id == target.id,
                UserPortfolioOverviewGrant.valid_from <= today,
                UserPortfolioOverviewGrant.valid_to.is_(None),
            )
            .all()
        )
        current_by_key: dict[tuple, list[tuple[type, int]]] = {}
        for row in current_scope_grants:
            current_by_key.setdefault(_stored_scope_grant_key(row), []).append(
                (UserScopeGrant, int(row.id))
            )
        for row in current_overview_grants:
            current_by_key.setdefault(_stored_overview_grant_key(row), []).append(
                (UserPortfolioOverviewGrant, int(row.id))
            )
        requested_grant_keys = {_resolved_grant_key(item) for item in resolved_grants}
        preserved_grants: dict[tuple, tuple[type, int]] = {
            key: rows[0]
            for key, rows in current_by_key.items()
            if len(rows) == 1 and key in requested_grant_keys
        }
        preserved_ids = {
            model: {
                row_id
                for preserved_model, row_id in preserved_grants.values()
                if preserved_model is model
            }
            for model in (UserScopeGrant, UserPortfolioOverviewGrant)
        }

        # 整包事务替换当前及未来授权；已结束的历史记录保留。
        # 当天记录由不可变修订表完整留痕后再删除重建；较早记录截止到昨天。
        for model in (UserScopeGrant, UserPortfolioOverviewGrant, UserCapability):
            rows = db.query(model).filter(model.user_id == target.id).all()
            for row in rows:
                if int(row.id) in preserved_ids.get(model, set()):
                    continue
                if row.valid_to is not None and row.valid_to < today:
                    continue
                if row.valid_from < today:
                    row.valid_to = today - timedelta(days=1)
                else:
                    db.delete(row)
        db.flush()
        for item in resolved_grants:
            if _resolved_grant_key(item) in preserved_grants:
                continue
            if item["scope_type"] == "portfolio_overview":
                db.add(
                    UserPortfolioOverviewGrant(
                        user_id=int(target.id),
                        portfolio_id=item["portfolio_id"],
                        include_future_buildings=item["include_future_buildings"],
                        valid_from=today,
                        valid_to=None,
                        granted_by=operator_id,
                        granted_at=changed_at,
                    )
                )
            else:
                db.add(
                    UserScopeGrant(
                        user_id=int(target.id),
                        scope_type=item["scope_type"],
                        portfolio_id=item["portfolio_id"],
                        building_id=item["building_id"],
                        access_level=item["access_level"],
                        include_future_buildings=item["include_future_buildings"],
                        valid_from=today,
                        valid_to=None,
                        granted_by=operator_id,
                        granted_at=changed_at,
                    )
                )
        for capability in requested_capabilities:
            db.add(
                UserCapability(
                    user_id=int(target.id),
                    capability=capability,
                    valid_from=today,
                    valid_to=None,
                    granted_by=operator_id,
                    granted_at=changed_at,
                )
            )
        db.flush()

        current_revision = int(target.authorization_revision or 0)
        cas_result = db.execute(
            update(User)
            .where(
                User.id == target.id,
                User.authorization_revision == current_revision,
            )
            .values(
                authorization_revision=current_revision + 1,
                session_version=func.coalesce(User.session_version, 0) + 1,
            ),
            execution_options={"synchronize_session": False},
        )
        if cas_result.rowcount != 1:
            raise HTTPException(409, "用户授权已被其他管理员修改，请刷新后重试")
        db.flush()
        db.expire_all()
        after = describe_user_authorization(db, user_id)
        after_history = _history_document(db, after)
        if int(after["authorization_revision"]) != current_revision + 1:
            raise RuntimeError("authorization revision compare-and-swap failed")

        db.add(
            UserAuthorizationRevision(
                target_user_id=int(target.id),
                revision=current_revision + 1,
                global_revision=global_revision,
                operator_user_id=operator_id,
                operator_username=current_operator.username or "system",
                before_state=dump_changes(before_history),
                after_state=dump_changes(after_history),
                created_at=changed_at,
            )
        )
        log_action(
            db,
            None,
            "authorization_replace",
            dump_changes({
                "user_id": int(target.id),
                "revision": current_revision + 1,
                "global_revision": global_revision,
                "before": {"grants": before["grants"], "capabilities": before["capabilities"]},
                "after": {"grants": after["grants"], "capabilities": after["capabilities"]},
            }),
            current_operator.username or "system",
            commit=False,
            target_type="user_authorization",
            target_id=int(target.id),
            object_scope=ObjectScope("global"),
        )
        db.commit()
    except OperationalError as exc:
        db.rollback()
        if _is_concurrent_write_error(exc):
            raise HTTPException(409, "授权正在被其他管理员修改，请刷新后重试") from exc
        raise
    except Exception:
        db.rollback()
        raise
    return after
