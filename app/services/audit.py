from __future__ import annotations

import io
from datetime import date, datetime, time, timedelta
from collections.abc import Sequence
from urllib.parse import quote

import openpyxl
from fastapi.responses import StreamingResponse
from openpyxl.styles import Font
from sqlalchemy.orm import Session, selectinload

from app.core.time import today_in_timezone
from app.core.excel_safety import sanitize_workbook
from app.core.workbook_localization import localize_workbook
from app.models import Contract, OperationLog
from app.services.common import customer_display_name


ACTION_LABELS = {
    "finance_handoff_export": "导出财务交接包",
    "finance_mapping_save": "保存客户财务映射",
    "financial_plan_save": "保存预算预测方案",
    "financial_plan_adopt": "采用预算预测版本",
    "financial_expense_save": "保存项目费用",
    "create": "创建合同",
    "update": "修改合同",
    "delete": "删除合同",
    "import": "导入合同",
    "restore": "恢复合同",
    "terminate": "退租",
    "change_confirm": "变更签确认",
    "room_create": "新增房间",
    "room_update": "修改房间",
    "room_delete": "删除房间",
    "room_restore": "恢复房间",
    "room_clear": "清空房间回收站",
    "receivable_update": "更新应收状态",
    "receivable_payment": "登记收款核销",
    "receivable_payment_delete": "删除收款核销",
    "document_generate": "生成业务文档",
    "daily_report_export": "导出经营日报",
    "rent_analytics_export": "导出租金时间分析",
    "room_expand": "扩租",
    "room_reduce": "减租",
    "room_swap": "换房",
    "rent_term_create": "新增租金条款",
    "rent_term_update": "修改租金条款",
    "rent_term_delete": "删除租金条款",
    "customer_create": "新增客户",
    "customer_update": "修改客户",
    "customer_profile_create": "建立楼宇客户资料",
    "customer_profile_update": "修改楼宇客户资料",
    "customer_followup_create": "新增客户跟进",
    "customer_followup_update": "修改客户跟进",
    "customer_followup_delete": "删除客户跟进",
    "customer_followup_done": "完成客户跟进",
    "customer_import": "导入客户",
    "customer_profile_import": "导入楼宇客户资料",
    "customer_master_import": "导入共享客户主档",
    "followup_import": "导入客户跟进",
    "deposit_create": "登记应收押金",
    "deposit_update": "修改押金",
    "deposit_receive": "登记押金收取",
    "deposit_refund": "登记押金退还",
    "deposit_offset": "登记押金冲抵",
    "attachment_upload": "上传附件",
    "attachment_update": "编辑附件",
    "attachment_delete": "删除附件",
    "config_update": "修改系统配置",
    "authorization_replace": "调整用户授权",
    "gate_change": "调整楼宇展示与合计",
    "client_error": "前端错误",
}

OPERATOR_LABELS = {
    "system": "系统任务",
    "seed_demo_data": "模拟数据脚本",
}


def get_action_label(action: str | None) -> str:
    if not action:
        return ""
    return ACTION_LABELS.get(action, action)


def get_operator_label(operator: str | None) -> str:
    if not operator:
        return "系统任务"
    return OPERATOR_LABELS.get(operator, operator)


def _date_start(value: date | None) -> datetime | None:
    if not value:
        return None
    return datetime.combine(value, time.min)


def _date_end(value: date | None) -> datetime | None:
    if not value:
        return None
    return datetime.combine(value + timedelta(days=1), time.min)


def get_audit_logs(
    db: Session,
    page: int,
    page_size: int,
    operator: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    building_ids: Sequence[int] | None = None,
    portfolio_id: int | None = None,
    scope_type: str | None = None,
) -> dict:
    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 50)), 200)
    scope_ids = tuple(dict.fromkeys(int(item) for item in building_ids)) if building_ids is not None else None
    query = db.query(OperationLog)
    if scope_type is not None:
        query = query.filter(OperationLog.scope_type == scope_type)
    if scope_ids is not None:
        # Business audit is visible at the immutable building boundary.  A
        # portfolio log has no membership snapshot, so exposing it through a
        # later partial/future portfolio grant would be unsafe.
        query = query.filter(
            OperationLog.scope_type == "building",
            OperationLog.building_id.in_(scope_ids),
            OperationLog.portfolio_id.is_(None),
        )
    query = query.order_by(OperationLog.timestamp.desc())
    if operator:
        query = query.filter(OperationLog.operator == operator)
    if action:
        query = query.filter(OperationLog.action == action)
    if target_type:
        query = query.filter(OperationLog.target_type == target_type)
    if target_id is not None:
        query = query.filter(OperationLog.target_id == target_id)
    start_at = _date_start(start_date)
    end_before = _date_end(end_date)
    if start_at:
        query = query.filter(OperationLog.timestamp >= start_at)
    if end_before:
        query = query.filter(OperationLog.timestamp < end_before)

    total = query.count()
    logs = query.offset((page - 1) * page_size).limit(page_size).all()
    contract_ids = [log.contract_id for log in logs if log.contract_id]
    contracts_query = db.query(Contract).filter(Contract.id.in_(contract_ids)) if contract_ids else None
    if contracts_query is not None and scope_ids is not None:
        contracts_query = contracts_query.filter(Contract.building_id.in_(scope_ids))
    contracts = {item.id: item for item in contracts_query.all()} if contracts_query is not None else {}

    items = []
    for log in logs:
        contract = contracts.get(log.contract_id)
        items.append(
            {
                "id": log.id,
                "contract_id": log.contract_id,
                "contract_no": contract.contract_no if contract else "",
                "tenant_name": customer_display_name(contract) if contract else "",
                "target_type": log.target_type or ("contract" if log.contract_id else ""),
                "target_id": log.target_id if log.target_id is not None else log.contract_id,
                "action": log.action,
                "action_label": get_action_label(log.action),
                "changes": log.changes or "",
                "operator": log.operator or "system",
                "operator_label": get_operator_label(log.operator),
                "timestamp": log.timestamp.isoformat() if log.timestamp else "",
            }
        )

    def distinct_values(column):
        values = db.query(column)
        if scope_type is not None:
            values = values.filter(OperationLog.scope_type == scope_type)
        if scope_ids is not None:
            values = values.filter(
                OperationLog.scope_type == "building",
                OperationLog.building_id.in_(scope_ids),
                OperationLog.portfolio_id.is_(None),
            )
        return [value for (value,) in values.distinct().all() if value]

    operators = distinct_values(OperationLog.operator)
    actions = distinct_values(OperationLog.action)
    target_types = distinct_values(OperationLog.target_type)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "operators": operators,
        "actions": [{"value": value, "label": get_action_label(value)} for value in actions],
        "target_types": sorted(target_types),
    }


def export_audit_logs(
    db: Session,
    *,
    building_ids: Sequence[int] | None = None,
    portfolio_id: int | None = None,
    scope_type: str | None = None,
) -> StreamingResponse:
    scope_ids = tuple(dict.fromkeys(int(item) for item in building_ids)) if building_ids is not None else None
    logs_query = db.query(OperationLog)
    if scope_type is not None:
        logs_query = logs_query.filter(OperationLog.scope_type == scope_type)
    if scope_ids is not None:
        logs_query = logs_query.filter(
            OperationLog.scope_type == "building",
            OperationLog.building_id.in_(scope_ids),
            OperationLog.portfolio_id.is_(None),
        )
    logs = logs_query.order_by(OperationLog.timestamp.desc()).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "操作日志"

    headers = ["时间", "操作人", "动作", "合同编号", "客户名称", "变更内容"]
    ws.append(headers)
    for idx, header in enumerate(headers, 1):
        ws.cell(1, idx).font = Font(bold=True)

    contract_ids = {log.contract_id for log in logs if log.contract_id}
    contracts_by_id = {}
    if contract_ids:
        contracts_by_id = {
            contract.id: contract
            for contract in db.query(Contract)
            .options(selectinload(Contract.customer))
            .filter(Contract.id.in_(contract_ids))
            .filter(Contract.building_id.in_(scope_ids) if scope_ids is not None else True)
            .all()
        }
    for log in logs:
        contract = contracts_by_id.get(log.contract_id) if log.contract_id else None
        ws.append(
            [
                log.timestamp.strftime("%Y-%m-%d %H:%M:%S") if log.timestamp else "",
                get_operator_label(log.operator),
                get_action_label(log.action),
                contract.contract_no if contract else "",
                customer_display_name(contract) if contract else "",
                log.changes or "",
            ]
        )

    for idx, width in enumerate([20, 12, 14, 16, 16, 50], 1):
        ws.column_dimensions[chr(64 + idx)].width = width

    localize_workbook(wb)

    sanitize_workbook(wb)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"操作日志_{today_in_timezone().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
