from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.integrations.adapters import adapter_for
from app.integrations.crypto import decrypt_secret, encrypt_secret
from app.models import IntegrationConnection, IntegrationEvent, IntegrationSyncRun


PROVIDER_DEFINITIONS = [
    {"value": "jde", "label": "JDE", "capabilities": ["import", "push", "bidirectional"], "reserved": True},
    {"value": "oa", "label": "OA", "capabilities": ["import", "push", "bidirectional"], "reserved": True},
    {"value": "finance", "label": "财务系统", "capabilities": ["import", "push", "bidirectional"], "reserved": True},
    {"value": "saas", "label": "SaaS", "capabilities": ["import", "push", "bidirectional"], "reserved": True},
    {"value": "rest_webhook", "label": "REST Webhook", "capabilities": ["push"], "reserved": False},
    {"value": "mcp_server", "label": "MCP Server", "capabilities": ["export"], "reserved": True},
    {"value": "mcp_client", "label": "MCP Client", "capabilities": ["import", "push", "bidirectional"], "reserved": True},
    {"value": "custom", "label": "自定义", "capabilities": ["import", "push", "bidirectional"], "reserved": True},
]
PROVIDER_LABELS = {item["value"]: item["label"] for item in PROVIDER_DEFINITIONS}
DIRECTION_LABELS = {"import": "导入", "push": "推送", "bidirectional": "双向"}


def _parse_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _dump_json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"), default=str)


def _validate_provider(provider: str) -> str:
    value = (provider or "").strip()
    if value not in PROVIDER_LABELS:
        raise HTTPException(400, "不支持的连接器类型")
    return value


def _validate_direction(direction: str) -> str:
    value = (direction or "bidirectional").strip()
    if value not in DIRECTION_LABELS:
        raise HTTPException(400, "不支持的同步方向")
    return value


def serialize_connection(connection: IntegrationConnection) -> dict:
    return {
        "id": connection.id,
        "name": connection.name,
        "provider": connection.provider,
        "provider_label": PROVIDER_LABELS.get(connection.provider, connection.provider),
        "direction": connection.direction or "bidirectional",
        "direction_label": DIRECTION_LABELS.get(connection.direction or "bidirectional", connection.direction or ""),
        "enabled": bool(connection.enabled),
        "config": _parse_json(connection.config_json),
        "secret_configured": bool(connection.secret_json),
        "created_at": connection.created_at,
        "updated_at": connection.updated_at,
    }


def provider_definitions() -> list[dict]:
    return [
        {
            **item,
            "direction_labels": DIRECTION_LABELS,
            "config_fields": ["endpoint_url", "base_url", "tenant_id", "remark"],
            "secret_fields": ["endpoint_url", "bearer_token", "api_key", "client_secret"],
        }
        for item in PROVIDER_DEFINITIONS
    ]


def list_connections(db: Session) -> list[dict]:
    items = db.query(IntegrationConnection).order_by(IntegrationConnection.created_at.desc(), IntegrationConnection.id.desc()).all()
    return [serialize_connection(item) for item in items]


def create_connection(db: Session, payload) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "连接器名称不能为空")
    provider = _validate_provider(payload.provider)
    direction = _validate_direction(payload.direction)
    try:
        secret_json = encrypt_secret(payload.secret)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    connection = IntegrationConnection(
        name=name,
        provider=provider,
        direction=direction,
        enabled=payload.enabled,
        config_json=_dump_json(payload.config),
        secret_json=secret_json,
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return serialize_connection(connection)


def update_connection(db: Session, connection_id: int, payload) -> dict:
    connection = db.query(IntegrationConnection).filter(IntegrationConnection.id == connection_id).first()
    if not connection:
        raise HTTPException(404, "连接器不存在")
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "连接器名称不能为空")
    connection.name = name
    connection.provider = _validate_provider(payload.provider)
    connection.direction = _validate_direction(payload.direction)
    connection.enabled = payload.enabled
    connection.config_json = _dump_json(payload.config)
    if payload.secret is not None:
        try:
            connection.secret_json = encrypt_secret(payload.secret)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    connection.updated_at = datetime.now()
    db.commit()
    db.refresh(connection)
    return serialize_connection(connection)


def delete_connection(db: Session, connection_id: int) -> dict:
    connection = db.query(IntegrationConnection).filter(IntegrationConnection.id == connection_id).first()
    if not connection:
        raise HTTPException(404, "连接器不存在")
    db.query(IntegrationEvent).filter(IntegrationEvent.connection_id == connection_id).update(
        {"connection_id": None},
        synchronize_session=False,
    )
    db.query(IntegrationSyncRun).filter(IntegrationSyncRun.connection_id == connection_id).delete(synchronize_session=False)
    db.delete(connection)
    db.commit()
    return {"message": "连接器已删除"}


def _connection_secret(connection: IntegrationConnection) -> dict[str, Any]:
    try:
        return decrypt_secret(connection.secret_json)
    except Exception as exc:
        raise HTTPException(400, f"连接器密钥无法解密：{exc}") from exc


def test_connection(db: Session, connection_id: int) -> dict:
    connection = db.query(IntegrationConnection).filter(IntegrationConnection.id == connection_id).first()
    if not connection:
        raise HTTPException(404, "连接器不存在")
    result = adapter_for(connection.provider).test_connection(_parse_json(connection.config_json), _connection_secret(connection))
    return {"success": result.success, "message": result.message}


def trigger_sync(db: Session, connection_id: int) -> dict:
    connection = db.query(IntegrationConnection).filter(IntegrationConnection.id == connection_id).first()
    if not connection:
        raise HTTPException(404, "连接器不存在")
    result = adapter_for(connection.provider).sync(_parse_json(connection.config_json), _connection_secret(connection), connection.direction or "bidirectional")
    run = IntegrationSyncRun(
        connection_id=connection.id,
        direction=connection.direction or "bidirectional",
        status="success" if result.success else "reserved",
        message=result.message,
        started_at=datetime.now(),
        finished_at=datetime.now(),
    )
    db.add(run)
    db.commit()
    return {"success": result.success, "message": result.message, "run_id": run.id}


def enqueue_event(db: Session, resource_type: str, event_type: str, resource_id: int | None, payload: dict[str, Any]) -> int:
    connections = (
        db.query(IntegrationConnection)
        .filter(
            IntegrationConnection.enabled.is_(True),
            IntegrationConnection.direction.in_(["push", "bidirectional"]),
        )
        .all()
    )
    for connection in connections:
        db.add(
            IntegrationEvent(
                connection_id=connection.id,
                resource_type=resource_type,
                event_type=event_type,
                resource_id=resource_id,
                payload_json=_dump_json(payload),
                status="pending",
            )
        )
    if connections:
        db.commit()
    return len(connections)


def serialize_event(event: IntegrationEvent, connection: IntegrationConnection | None = None) -> dict:
    return {
        "id": event.id,
        "connection_id": event.connection_id,
        "connection_name": connection.name if connection else "",
        "event_type": event.event_type,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "status": event.status,
        "attempts": event.attempts or 0,
        "last_error": event.last_error or "",
        "payload": _parse_json(event.payload_json),
        "created_at": event.created_at,
        "updated_at": event.updated_at,
    }


def list_events(db: Session, status: str | None = None, limit: int = 100) -> dict:
    query = db.query(IntegrationEvent).order_by(IntegrationEvent.created_at.desc(), IntegrationEvent.id.desc())
    if status:
        query = query.filter(IntegrationEvent.status == status)
    total = query.count()
    events = query.limit(max(1, min(limit, 500))).all()
    connection_ids = {event.connection_id for event in events if event.connection_id}
    connections = {
        item.id: item
        for item in db.query(IntegrationConnection).filter(IntegrationConnection.id.in_(connection_ids)).all()
    } if connection_ids else {}
    return {"total": total, "items": [serialize_event(event, connections.get(event.connection_id)) for event in events]}


def process_event(db: Session, event: IntegrationEvent) -> dict:
    connection = db.query(IntegrationConnection).filter(IntegrationConnection.id == event.connection_id).first()
    if not connection or not connection.enabled:
        event.status = "skipped"
        event.last_error = "连接器不存在或已禁用"
        event.updated_at = datetime.now()
        db.commit()
        return serialize_event(event, connection)

    result = adapter_for(connection.provider).push_event(
        _parse_json(connection.config_json),
        _connection_secret(connection),
        {
            "id": event.id,
            "event_type": event.event_type,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "payload": _parse_json(event.payload_json),
            "created_at": event.created_at.isoformat() if event.created_at else "",
        },
    )
    event.attempts = (event.attempts or 0) + 1
    if result.success:
        event.status = "sent"
    elif connection.provider != "rest_webhook":
        event.status = "skipped"
    else:
        event.status = "failed"
    event.last_error = "" if result.success else result.message
    event.updated_at = datetime.now()
    db.commit()
    db.refresh(event)
    return serialize_event(event, connection)


def retry_event(db: Session, event_id: int) -> dict:
    event = db.query(IntegrationEvent).filter(IntegrationEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "集成事件不存在")
    event.status = "pending"
    event.updated_at = datetime.now()
    db.commit()
    return process_event(db, event)


def process_pending_events(db: Session, limit: int = 20) -> int:
    events = (
        db.query(IntegrationEvent)
        .filter(IntegrationEvent.status.in_(["pending", "failed"]), IntegrationEvent.attempts < 5)
        .order_by(IntegrationEvent.created_at.asc(), IntegrationEvent.id.asc())
        .limit(limit)
        .all()
    )
    for event in events:
        process_event(db, event)
    return len(events)
