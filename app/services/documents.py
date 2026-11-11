from __future__ import annotations

from datetime import date
from html import escape
from app.core.presentation_units import view_amount, conversion_note
from app.core.localization import display, request_locale, localize_static_html
from app.core.currency_unit import request_currency
import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.time import today_in_timezone
from app.models import Contract, ReceivablePayment, SystemConfig
from app.schemas import LeaseDocumentPayload
from app.services.common import dump_changes, log_action
from app.services.contracts import get_contract, get_monthly_receivables
from app.services.legacy_demo_a_scope import ObjectScope
from app.services.property_scope import SelectedPropertyScope


DOCUMENT_TYPE_LABELS = {
    "payment_notice": "缴款通知书",
    "dunning_notice": "催收通知",
    "receipt": "收据",
}
DEFAULT_ISSUER = "租赁管理系统"
DEFAULT_PAYMENT_INSTRUCTION = "请按合同约定或双方确认的付款账户及时支付。"


def _money(value: float | int | None) -> str:
    return f"{view_amount(value):,.2f}"


def _display(value: object) -> str:
    if value in (None, ""):
        return "--"
    return str(value)


def _customer_name(contract: Contract) -> str:
    customer = getattr(contract, "customer", None)
    customer_value = (getattr(customer, "name", "") or "").strip() if customer else ""
    return customer_value or (contract.tenant_name or "").strip() or "--"


def _issuer_name(db: Session) -> str:
    for key in ("document_issuer", "company_name", "project_name"):
        config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if config and (config.value or "").strip():
            return config.value.strip()
    return DEFAULT_ISSUER


def _filename_part(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", text, flags=re.UNICODE)
    return text.strip("-") or "document"


def resolve_lease_document_contract(
    db: Session,
    payload: LeaseDocumentPayload,
    *,
    scope: SelectedPropertyScope,
) -> Contract:
    document_type = (payload.document_type or "").strip()
    if document_type not in DOCUMENT_TYPE_LABELS:
        raise HTTPException(400, "文档类型无效")
    contract_id = payload.contract_id
    if document_type == "receipt" and payload.payment_id:
        payment = (
            db.query(ReceivablePayment)
            .join(Contract, Contract.id == ReceivablePayment.contract_id)
            .filter(
                ReceivablePayment.id == payload.payment_id,
                Contract.building_id.in_(scope.building_ids or (-1,)),
                Contract.is_deleted.is_(False),
            )
            .first()
        )
        if not payment:
            raise HTTPException(404, "收款记录不存在或无权访问")
        if contract_id is not None and int(contract_id) != int(payment.contract_id):
            raise HTTPException(404, "收款记录不存在或无权访问")
        contract_id = payment.contract_id
    if not contract_id:
        if document_type == "receipt":
            raise HTTPException(400, "生成收据需要收款记录或合同账期")
        label = "缴款通知书" if document_type == "payment_notice" else "催收通知"
        raise HTTPException(400, f"生成{label}需要合同")
    return get_contract(db, int(contract_id), scope=scope, for_write=True)


def _receipt_payment(
    db: Session,
    payload: LeaseDocumentPayload,
    contract: Contract,
) -> ReceivablePayment:
    query = db.query(ReceivablePayment)
    if payload.payment_id:
        payment = query.filter(
            ReceivablePayment.id == payload.payment_id,
            ReceivablePayment.contract_id == contract.id,
        ).first()
        if not payment:
            raise HTTPException(404, "收款记录不存在")
        return payment

    if not payload.month:
        raise HTTPException(400, "生成收据需要收款记录或合同账期")
    payment = (
        query.filter(ReceivablePayment.contract_id == contract.id, ReceivablePayment.period_month == payload.month)
        .order_by(ReceivablePayment.payment_date.desc(), ReceivablePayment.id.desc())
        .first()
    )
    if not payment:
        raise HTTPException(404, "该账期没有可生成收据的收款记录")
    return payment


def _receivable_row(
    db: Session,
    contract: Contract,
    month: str | None,
    scope: SelectedPropertyScope,
) -> tuple[str, dict]:
    report = get_monthly_receivables(
        db,
        month,
        contract_ids=[int(contract.id)],
        building_ids=scope.building_ids,
    )
    for row in report["rows"]:
        if row["contract_id"] == contract.id:
            return report["month"], row
    raise HTTPException(404, "该合同在所选账期没有应收记录")


def _render_html(
    *,
    document_type: str,
    title: str,
    issuer: str,
    notice_date: date,
    lead: str,
    rows: list[tuple[str, object]],
    payment_instruction: str,
    notes: str,
) -> str:
    row_html = "\n".join(
        f"<tr><th>{escape(display(label))}</th><td>{escape(_display(value))}</td></tr>"
        for label, value in rows
    )
    instruction_html = (
        f"<section class=\"document-block\"><h2>{escape(display('付款说明'))}</h2><p>{escape(payment_instruction)}</p></section>"
        if payment_instruction
        else ""
    )
    notes_html = (
        f"<section class=\"document-block\"><h2>{escape(display('备注'))}</h2><p>{escape(notes)}</p></section>"
        if notes
        else ""
    )
    return f"""<!doctype html>
<html lang="{request_locale.get()}">
<head>
  <meta charset="utf-8" />
  <title>{escape(display(title))}</title>
  <style>
    @page {{ size: A4; margin: 22mm 20mm; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: #111827;
      font: 14px/1.75 "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
      background: #f8fafc;
    }}
    .page {{
      width: 210mm;
      min-height: 297mm;
      margin: 0 auto;
      padding: 28mm 22mm;
      background: #fff;
    }}
    h1 {{
      margin: 0 0 18px;
      text-align: center;
      font-size: 28px;
      letter-spacing: 0.08em;
    }}
    h2 {{
      margin: 22px 0 8px;
      font-size: 16px;
    }}
    .meta {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 24px;
      color: #374151;
    }}
    .lead {{
      margin: 0 0 18px;
      font-size: 15px;
      text-indent: 2em;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 14px;
    }}
    th, td {{
      border: 1px solid #d1d5db;
      padding: 10px 12px;
      vertical-align: top;
    }}
    th {{
      width: 32%;
      background: #f3f4f6;
      text-align: left;
      font-weight: 700;
    }}
    .document-block p {{
      margin: 0;
      white-space: pre-wrap;
    }}
    .signature {{
      display: grid;
      justify-content: end;
      gap: 8px;
      margin-top: 36px;
      text-align: right;
      font-size: 15px;
    }}
    .print-note {{
      margin-top: 28px;
      color: #6b7280;
      font-size: 12px;
      text-align: center;
    }}
    @media print {{
      body {{ background: #fff; }}
      .page {{ width: auto; min-height: auto; margin: 0; padding: 0; }}
      .print-note {{ display: none; }}
    }}
  </style>
</head>
<body data-document-type="{escape(document_type)}">
  <main class="page">
    <h1>{escape(display(title))}</h1>
    <div class="meta">
      <span>{escape(display('出具方：'))}{escape(issuer)}</span>
      <span>{escape(display('生成日期：'))}{escape(notice_date.isoformat())}</span>
      <span>{escape(display('货币单位'))}: {request_currency.get()}</span>
    </div>
    <p class="lead">{escape(display(lead))}</p>
    <table>
      <tbody>
        {row_html}
      </tbody>
    </table>
    {instruction_html}
    {notes_html}
    <div class="signature">
      <strong>{escape(issuer)}</strong>
      <span>{escape(notice_date.isoformat())}</span>
    </div>
    <div class="print-note">{escape(display('本文件由租赁管理系统生成，可直接打印或另存为 PDF。'))}</div>
  </main>
<p style="font-size:10px;color:#667085">{escape(conversion_note())}</p>
</body>
</html>"""


def _payment_notice(
    db: Session,
    payload: LeaseDocumentPayload,
    operator: str,
    contract: Contract,
    scope: SelectedPropertyScope,
) -> dict:
    period_month, row = _receivable_row(db, contract, payload.month, scope)
    notice_date = payload.notice_date or today_in_timezone()
    issuer = _issuer_name(db)
    customer = _customer_name(contract)
    payment_instruction = (payload.payment_instruction or "").strip() or DEFAULT_PAYMENT_INSTRUCTION
    lead = (
        f"贵方 {period_month} 账期应付租赁款项合计人民币 {_money(row['total'])} 元，"
        f"截至本通知生成日已收人民币 {_money(row['received_amount'])} 元，"
        f"本月未收人民币 {_money(row['balance'])} 元。"
    )
    rows = [
        ("客户名称", customer),
        ("合同号", contract.contract_no or ""),
        ("房源", row.get("room_no") or ""),
        ("账期", period_month),
        ("应收金额（元）", _money(row.get("total"))),
        ("已收金额（元）", _money(row.get("received_amount"))),
        ("核销调整（元）", _money(row.get("adjustment_amount"))),
        ("未收金额（元）", _money(row.get("balance"))),
        ("付款状态", row.get("payment_status")),
        ("到期日", row.get("due_date")),
    ]
    return _finish_document(
        db,
        contract,
        operator,
        document_type="payment_notice",
        notice_date=notice_date,
        period_month=period_month,
        amount=row.get("balance") or row.get("total") or 0,
        html=_render_html(
            document_type="payment_notice",
            title=DOCUMENT_TYPE_LABELS["payment_notice"],
            issuer=issuer,
            notice_date=notice_date,
            lead=lead,
            rows=rows,
            payment_instruction=payment_instruction,
            notes=(payload.notes or "").strip(),
        ),
    )


def _dunning_notice(
    db: Session,
    payload: LeaseDocumentPayload,
    operator: str,
    contract: Contract,
    scope: SelectedPropertyScope,
) -> dict:
    period_month, row = _receivable_row(db, contract, payload.month, scope)
    notice_date = payload.notice_date or today_in_timezone()
    issuer = _issuer_name(db)
    customer = _customer_name(contract)
    payment_instruction = (payload.payment_instruction or "").strip() or DEFAULT_PAYMENT_INSTRUCTION
    overdue_amount = row.get("overdue_balance") or row.get("balance") or 0
    aging_days = int(row.get("aging_days") or 0)
    lead = (
        f"截至 {notice_date.isoformat()}，贵方 {period_month} 账期仍有本月逾期人民币 {_money(overdue_amount)} 元，"
        f"逾期天数 {aging_days} 天。请收到本通知后及时核对并安排付款。"
    )
    rows = [
        ("客户名称", customer),
        ("合同号", contract.contract_no or ""),
        ("房源", row.get("room_no") or ""),
        ("账期", period_month),
        ("应收金额（元）", _money(row.get("total"))),
        ("已收金额（元）", _money(row.get("received_amount"))),
        ("未收金额（元）", _money(row.get("balance"))),
        ("本月逾期（元）", _money(overdue_amount)),
        ("逾期天数", f"{aging_days} 天"),
        ("催收状态", row.get("dunning_status")),
        ("最近催收日", row.get("last_dunning_date")),
        ("下次跟进日", row.get("next_follow_up_date")),
        ("催收备注", row.get("dunning_notes")),
    ]
    return _finish_document(
        db,
        contract,
        operator,
        document_type="dunning_notice",
        notice_date=notice_date,
        period_month=period_month,
        amount=overdue_amount,
        html=_render_html(
            document_type="dunning_notice",
            title=DOCUMENT_TYPE_LABELS["dunning_notice"],
            issuer=issuer,
            notice_date=notice_date,
            lead=lead,
            rows=rows,
            payment_instruction=payment_instruction,
            notes=(payload.notes or "").strip(),
        ),
    )


def _receipt(
    db: Session,
    payload: LeaseDocumentPayload,
    operator: str,
    contract: Contract,
    scope: SelectedPropertyScope,
) -> dict:
    payment = _receipt_payment(db, payload, contract)
    period_month, row = _receivable_row(db, contract, payment.period_month, scope)
    notice_date = payload.notice_date or today_in_timezone()
    issuer = _issuer_name(db)
    customer = _customer_name(contract)
    lead = f"今收到 {customer} 支付 {period_month} 账期租赁款项人民币 {_money(payment.amount)} 元。"
    rows = [
        ("客户名称", customer),
        ("合同号", contract.contract_no or ""),
        ("房源", row.get("room_no") or ""),
        ("账期", period_month),
        ("收款日期", payment.payment_date.isoformat() if payment.payment_date else ""),
        ("收款金额（元）", _money(payment.amount)),
        ("收款方式", payment.payment_method or ""),
        ("收款类型", payment.allocation_type or ""),
        ("凭证/流水号", payment.reference_no or ""),
        ("备注", payment.notes or ""),
    ]
    return _finish_document(
        db,
        contract,
        operator,
        document_type="receipt",
        notice_date=notice_date,
        period_month=period_month,
        amount=payment.amount or 0,
        payment_id=payment.id,
        html=_render_html(
            document_type="receipt",
            title=DOCUMENT_TYPE_LABELS["receipt"],
            issuer=issuer,
            notice_date=notice_date,
            lead=lead,
            rows=rows,
            payment_instruction="",
            notes=(payload.notes or "").strip(),
        ),
    )


def _finish_document(
    db: Session,
    contract: Contract,
    operator: str,
    *,
    document_type: str,
    notice_date: date,
    period_month: str,
    amount: float,
    html: str,
    payment_id: int | None = None,
) -> dict:
    filename = "_".join(
        [
            _filename_part(document_type),
            _filename_part(contract.contract_no),
            _filename_part(period_month),
            _filename_part(payment_id or notice_date.isoformat()),
        ]
    ) + ".html"
    summary = {
        "document_type": document_type,
        "document_label": DOCUMENT_TYPE_LABELS[document_type],
        "contract_id": contract.id,
        "contract_no": contract.contract_no or "",
        "tenant_name": _customer_name(contract),
        "period_month": period_month,
        "payment_id": payment_id,
        "amount": round(float(amount or 0), 2),
        "filename": filename,
    }
    log_action(
        db,
        contract.id,
        "document_generate",
        dump_changes(summary),
        operator,
        target_type="document",
        target_id=contract.id,
        object_scope=ObjectScope(scope_type="building", building_id=int(contract.building_id)),
    )
    return {
        **summary,
        "notice_date": notice_date.isoformat(),
        "filename": filename,
        "content_type": "text/html; charset=utf-8",
        "html": html,
    }


def generate_lease_document(
    db: Session,
    payload: LeaseDocumentPayload,
    operator: str,
    *,
    scope: SelectedPropertyScope,
    parent_contract: Contract | None = None,
) -> dict:
    document_type = (payload.document_type or "").strip()
    if document_type not in DOCUMENT_TYPE_LABELS:
        raise HTTPException(400, "文档类型无效")
    contract = resolve_lease_document_contract(db, payload, scope=scope)
    if parent_contract is not None and int(parent_contract.id) != int(contract.id):
        raise HTTPException(404, "合同不存在或无权访问")
    if document_type == "payment_notice":
        return _payment_notice(db, payload, operator, contract, scope)
    if document_type == "dunning_notice":
        return _dunning_notice(db, payload, operator, contract, scope)
    return _receipt(db, payload, operator, contract, scope)
