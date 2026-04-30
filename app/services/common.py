from __future__ import annotations

from datetime import datetime
import json

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.models import Contract, OperationLog


def log_action(db: Session, contract_id: int | None, action: str, changes: str = "", operator: str = "system", commit: bool = True) -> None:
    log = OperationLog(contract_id=contract_id, action=action, changes=changes, operator=operator)
    db.add(log)
    if commit:
        db.commit()


def get_operator(request: Request | None) -> str:
    if request is None:
        return "system"
    token = request.cookies.get("token") or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        return "system"
    try:
        payload = decode_access_token(token)
        return payload.get("username", "system")
    except Exception:
        return "system"


def serialize_contract(contract: Contract) -> dict:
    return {
        "id": contract.id,
        "contract_no": contract.contract_no or "",
        "tower": contract.tower or "",
        "room_no": contract.room_no or "",
        "tenant_name": contract.tenant_name or "",
        "area": contract.area or 0,
        "unit_price": contract.unit_price or 0,
        "lease_type": contract.lease_type or "",
        "free_period": contract.free_period or 0,
        "monthly_rent": contract.monthly_rent or 0,
        "broker_fee": contract.broker_fee or 0,
        "net_rent": contract.net_rent,
        "start_date": contract.start_date,
        "end_date": contract.end_date,
        "status": contract.status,
        "days_to_expiry": contract.days_to_expiry,
        "disposal": contract.disposal or "",
        "contact_person": contract.contact_person or "",
        "contact_phone": contract.contact_phone or "",
        "contact_email": contract.contact_email or "",
        "deposit": contract.deposit or 0,
        "deposit_status": contract.deposit_status or "",
        "property_fee_rate": contract.property_fee_rate or 0,
        "monthly_property_fee": contract.monthly_property_fee,
        "prev_contract_no": contract.prev_contract_no or "",
        "notes": contract.notes or "",
        "pdf_path": contract.pdf_path or "",
        "created_at": contract.created_at,
        "updated_at": contract.updated_at,
    }


def parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def dump_changes(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)
