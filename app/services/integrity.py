from __future__ import annotations

from datetime import date
import io
import os
from collections import Counter
from urllib.parse import quote

from fastapi.responses import StreamingResponse
import openpyxl
from openpyxl.styles import Font
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Contract, OperationLog, Room, User


def run_integrity_check(db: Session) -> dict:
    settings = get_settings()
    issues: list[dict] = []
    contracts = db.query(Contract).filter(Contract.is_deleted.is_(False)).all()
    rooms = db.query(Room).filter(Room.is_deleted.is_(False)).all()
    room_no_set = {room.room_no for room in rooms if room.room_no}
    pdf_required_statuses = {"在租", "即将到期", "待生效", "逾期占用"}
    active_statuses = {"在租", "即将到期", "逾期占用"}

    for contract in contracts:
        if not contract.start_date or not contract.end_date:
            issues.append({"type": "missing_dates", "severity": "high", "contract_no": contract.contract_no, "detail": "缺少起租日或终止日"})
        elif contract.end_date <= contract.start_date:
            issues.append({"type": "invalid_dates", "severity": "high", "contract_no": contract.contract_no, "detail": "终止日早于起租日"})
        numeric_fields = [
            contract.area,
            contract.unit_price,
            contract.monthly_rent,
            contract.broker_fee,
            contract.deposit,
            contract.property_fee_rate,
            contract.free_period,
        ]
        if any((value or 0) < 0 for value in numeric_fields):
            issues.append({"type": "negative_values", "severity": "high", "contract_no": contract.contract_no, "detail": "面积、租金、单价、保证金、物业费或免租期存在负数"})
        if contract.status in active_statuses and contract.room_no and contract.room_no not in room_no_set:
            issues.append({"type": "missing_room_reference", "severity": "medium", "contract_no": contract.contract_no, "detail": f"合同关联的房间 {contract.room_no} 不在房间台账中"})

    active_rooms = [item.room_no for item in contracts if item.room_no and item.status in active_statuses]
    for room_no, count in Counter(active_rooms).items():
        if count > 1:
            issues.append({"type": "duplicate_room", "severity": "medium", "contract_no": room_no, "detail": f"室号 {room_no} 存在 {count} 份有效合同"})

    for contract in contracts:
        if contract.status in {"在租", "即将到期"} and (contract.monthly_rent or 0) <= 0:
            issues.append({"type": "zero_rent", "severity": "low", "contract_no": contract.contract_no, "detail": "月租金为 0"})

    missing_pdf_contracts = [
        contract
        for contract in contracts
        if contract.status in pdf_required_statuses and not (contract.pdf_path or "").strip()
    ]
    if missing_pdf_contracts:
        sample_contracts = "、".join(contract.contract_no for contract in missing_pdf_contracts[:5])
        sample_suffix = " 等" if len(missing_pdf_contracts) > 5 else ""
        issues.append(
            {
                "type": "missing_contract_pdfs",
                "severity": "medium",
                "contract_no": "",
                "detail": f"存在 {len(missing_pdf_contracts)} 份执行中合同未上传 PDF，如 {sample_contracts}{sample_suffix}",
            }
        )

    if settings.upload_dir.exists():
        all_pdfs = {
            item.name
            for item in settings.upload_dir.iterdir()
            if item.is_file() and item.suffix.lower() == ".pdf"
        }
        active_pdfs = {contract.pdf_path for contract in contracts if contract.pdf_path}
        orphan_count = len(all_pdfs - active_pdfs)
        if orphan_count:
            issues.append({"type": "orphan_pdfs", "severity": "low", "contract_no": "", "detail": f"存在 {orphan_count} 个孤立 PDF 文件"})

    db_path = settings.data_dir / "lease.db"
    total_contracts = db.query(Contract).count()
    deleted_contracts = db.query(Contract).filter(Contract.is_deleted.is_(True)).count()
    total_rooms = db.query(Room).count()
    total_users = db.query(User).count()
    total_logs = db.query(OperationLog).count()

    return {
        "issues": issues,
        "issue_count": len(issues),
        "db_stats": {
            "total_contracts": total_contracts,
            "active_contracts": total_contracts - deleted_contracts,
            "deleted_contracts": deleted_contracts,
            "total_rooms": total_rooms,
            "total_users": total_users,
            "total_logs": total_logs,
            "db_size_mb": round(os.path.getsize(db_path) / 1024 / 1024, 2) if db_path.exists() else 0,
        },
    }


def export_integrity_report(db: Session) -> StreamingResponse:
    report = run_integrity_check(db)
    workbook = openpyxl.Workbook()
    issues_sheet = workbook.active
    issues_sheet.title = "完整性问题"
    headers = ["类型", "严重级别", "合同/房间", "说明"]
    issues_sheet.append(headers)
    for idx, header in enumerate(headers, 1):
        issues_sheet.cell(1, idx).font = Font(bold=True)

    for issue in report["issues"]:
        issues_sheet.append(
            [
                issue.get("type", ""),
                issue.get("severity", ""),
                issue.get("contract_no", ""),
                issue.get("detail", ""),
            ]
        )

    stats_sheet = workbook.create_sheet("数据库概览")
    stats_sheet.append(["指标", "数值"])
    stats_sheet.cell(1, 1).font = Font(bold=True)
    stats_sheet.cell(1, 2).font = Font(bold=True)
    for key, value in report["db_stats"].items():
        stats_sheet.append([key, value])

    for sheet in (issues_sheet, stats_sheet):
        for column, width in enumerate([18, 12, 20, 80], 1):
            sheet.column_dimensions[openpyxl.utils.get_column_letter(column)].width = width

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    filename = f"完整性检查_{date.today().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
