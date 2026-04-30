from __future__ import annotations

from datetime import date
import io
from urllib.parse import quote

import openpyxl
from fastapi.responses import StreamingResponse
from openpyxl.styles import Font
from sqlalchemy.orm import Session

from app.models import Contract, OperationLog


ACTION_LABELS = {
    "create": "创建合同",
    "update": "修改合同",
    "delete": "删除合同",
    "import": "导入合同",
    "restore": "恢复合同",
    "room_create": "新增房间",
    "room_update": "修改房间",
    "room_delete": "删除房间",
    "room_restore": "恢复房间",
    "room_clear": "清空房间回收站",
}

OPERATOR_LABELS = {
    "system": "系统任务",
}


def get_action_label(action: str | None) -> str:
    if not action:
        return ""
    return ACTION_LABELS.get(action, action)


def get_operator_label(operator: str | None) -> str:
    if not operator:
        return "系统任务"
    return OPERATOR_LABELS.get(operator, operator)


def get_audit_logs(db: Session, page: int, page_size: int, operator: str | None, action: str | None) -> dict:
    query = db.query(OperationLog).order_by(OperationLog.timestamp.desc())
    if operator:
        query = query.filter(OperationLog.operator == operator)
    if action:
        query = query.filter(OperationLog.action == action)

    total = query.count()
    logs = query.offset((page - 1) * page_size).limit(page_size).all()
    contract_ids = [log.contract_id for log in logs if log.contract_id]
    contracts = {item.id: item for item in db.query(Contract).filter(Contract.id.in_(contract_ids)).all()} if contract_ids else {}

    items = []
    for log in logs:
        contract = contracts.get(log.contract_id)
        items.append(
            {
                "id": log.id,
                "contract_id": log.contract_id,
                "contract_no": contract.contract_no if contract else "",
                "tenant_name": contract.tenant_name if contract else "",
                "action": log.action,
                "action_label": get_action_label(log.action),
                "changes": log.changes or "",
                "operator": log.operator or "system",
                "operator_label": get_operator_label(log.operator),
                "timestamp": log.timestamp.isoformat() if log.timestamp else "",
            }
        )

    operators = [value for (value,) in db.query(OperationLog.operator).distinct().all() if value]
    actions = [value for (value,) in db.query(OperationLog.action).distinct().all() if value]
    return {
        "items": items,
        "total": total,
        "page": page,
        "operators": operators,
        "actions": [{"value": value, "label": get_action_label(value)} for value in actions],
    }


def export_audit_logs(db: Session) -> StreamingResponse:
    logs = db.query(OperationLog).order_by(OperationLog.timestamp.desc()).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "操作日志"

    headers = ["时间", "操作人", "动作", "合同编号", "客户名称", "变更内容"]
    ws.append(headers)
    for idx, header in enumerate(headers, 1):
        ws.cell(1, idx).font = Font(bold=True)

    for log in logs:
        contract = db.query(Contract).filter(Contract.id == log.contract_id).first() if log.contract_id else None
        ws.append(
            [
                log.timestamp.strftime("%Y-%m-%d %H:%M:%S") if log.timestamp else "",
                get_operator_label(log.operator),
                get_action_label(log.action),
                contract.contract_no if contract else "",
                contract.tenant_name if contract else "",
                log.changes or "",
            ]
        )

    for idx, width in enumerate([20, 12, 14, 16, 16, 50], 1):
        ws.column_dimensions[chr(64 + idx)].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"操作日志_{date.today().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
