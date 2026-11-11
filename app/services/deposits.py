from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.time import now_in_timezone, today_in_timezone
from app.core.money import round_money
from app.models import Contract, ContractDeposit
from app.schemas import DepositActionPayload, DepositCreate, DepositUpdate
from app.services.common import deposit_status, dump_changes, log_action, serialize_deposit
from app.services.legacy_demo_a_scope import ObjectScope


DEPOSIT_TYPES = {"履约保证金", "租赁押金", "其他"}


def _contract_log_scope(contract: Contract) -> ObjectScope:
    if contract.building_id is None:
        raise HTTPException(409, "合同尚未明确楼宇归属")
    return ObjectScope(scope_type="building", building_id=int(contract.building_id))


def _payload_dict(payload, *, partial: bool = False) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=partial)
    return payload.dict(exclude_unset=partial)


def _money(value) -> float:
    return round_money(float(value or 0))


def _clean_deposit_type(value: str | None) -> str:
    deposit_type = (value or "租赁押金").strip()
    if deposit_type not in DEPOSIT_TYPES:
        raise HTTPException(400, "押金类型无效")
    return deposit_type


def _validate_amounts(expected: float, received: float, refunded: float, offset: float) -> None:
    if min(expected, received, refunded, offset) < 0:
        raise HTTPException(400, "押金金额不得为负数")
    if round_money(refunded + offset) > received:
        raise HTTPException(400, "已退加已冲抵金额不得超过已收押金")


def _sync_status(deposit: ContractDeposit) -> None:
    deposit.expected_amount = _money(deposit.expected_amount)
    deposit.received_amount = _money(deposit.received_amount)
    deposit.refunded_amount = _money(deposit.refunded_amount)
    deposit.offset_amount = _money(deposit.offset_amount)
    _validate_amounts(
        deposit.expected_amount,
        deposit.received_amount,
        deposit.refunded_amount,
        deposit.offset_amount,
    )
    deposit.status = deposit_status(
        deposit.expected_amount,
        deposit.received_amount,
        deposit.refunded_amount,
        deposit.offset_amount,
    )


def _append_note(existing: str | None, note: str | None) -> str:
    text = (note or "").strip()
    if not text:
        return existing or ""
    if not existing:
        return text
    return f"{existing}\n{text}"


def get_deposit(contract: Contract, deposit_id: int) -> ContractDeposit:
    for deposit in contract.deposits or []:
        if deposit.id == deposit_id:
            return deposit
    raise HTTPException(404, "押金记录不存在")


def create_deposit(db: Session, contract: Contract, payload: DepositCreate, operator: str) -> dict:
    data = _payload_dict(payload)
    deposit = ContractDeposit(
        contract_id=contract.id,
        customer_id=contract.customer_id,
        deposit_type=_clean_deposit_type(data.get("deposit_type")),
        expected_amount=_money(data.get("expected_amount")),
        received_amount=0,
        refunded_amount=0,
        offset_amount=0,
        due_date=data.get("due_date"),
        notes=(data.get("notes") or "").strip(),
    )
    _sync_status(deposit)
    db.add(deposit)
    db.flush()
    log_action(
        db,
        contract.id,
        "deposit_create",
        dump_changes({"deposit_id": deposit.id, "expected_amount": deposit.expected_amount, "deposit_type": deposit.deposit_type}),
        operator,
        commit=False,
        target_type="deposit",
        target_id=deposit.id,
        object_scope=_contract_log_scope(contract),
    )
    db.commit()
    db.refresh(deposit)
    return serialize_deposit(deposit)


def _guard_finance_link(db, deposit):
    from app.finance_models import FinanceAllocation
    if db.query(FinanceAllocation.id).filter_by(deposit_id=deposit.id, voided=False).first():
        raise HTTPException(409, "押金已关联资金流水，请在经营财务核对中登记或撤销收退款")


def update_deposit(db: Session, contract: Contract, deposit: ContractDeposit, payload: DepositUpdate, operator: str) -> dict:
    _guard_finance_link(db, deposit)
    data = _payload_dict(payload, partial=True)
    if "deposit_type" in data:
        data["deposit_type"] = _clean_deposit_type(data.get("deposit_type"))
    for key in ("expected_amount", "received_amount", "refunded_amount", "offset_amount"):
        if key in data:
            data[key] = _money(data.get(key))
    if "notes" in data:
        data["notes"] = data.get("notes") or ""
    data["customer_id"] = contract.customer_id

    changes = {}
    for key, value in data.items():
        old_value = getattr(deposit, key)
        if str(old_value or "") != str(value or ""):
            changes[key] = {"old": str(old_value or ""), "new": str(value or "")}
            setattr(deposit, key, value)

    _sync_status(deposit)
    deposit.updated_at = now_in_timezone()
    if changes:
        log_action(
            db,
            contract.id,
            "deposit_update",
            dump_changes({"deposit_id": deposit.id, "changes": changes, "status": deposit.status}),
            operator,
            commit=False,
            target_type="deposit",
            target_id=deposit.id,
            object_scope=_contract_log_scope(contract),
        )
    db.commit()
    db.refresh(deposit)
    return serialize_deposit(deposit)


def receive_deposit(db: Session, contract: Contract, deposit: ContractDeposit, payload: DepositActionPayload, operator: str) -> dict:
    _guard_finance_link(db, deposit)
    amount = _money(payload.amount)
    if amount <= 0:
        raise HTTPException(400, "押金收取金额必须大于 0")
    deposit.customer_id = contract.customer_id
    deposit.received_amount = _money(_money(deposit.received_amount) + amount)
    deposit.received_date = payload.action_date or today_in_timezone()
    deposit.notes = _append_note(deposit.notes, payload.notes)
    deposit.updated_at = now_in_timezone()
    _sync_status(deposit)
    log_action(
        db,
        contract.id,
        "deposit_receive",
        dump_changes({"deposit_id": deposit.id, "amount": amount, "status": deposit.status}),
        operator,
        commit=False,
        target_type="deposit",
        target_id=deposit.id,
        object_scope=_contract_log_scope(contract),
    )
    db.commit()
    db.refresh(deposit)
    return serialize_deposit(deposit)


def refund_deposit(db: Session, contract: Contract, deposit: ContractDeposit, payload: DepositActionPayload, operator: str) -> dict:
    _guard_finance_link(db, deposit)
    amount = _money(payload.amount)
    if amount <= 0:
        raise HTTPException(400, "押金退还金额必须大于 0")
    available = _money(_money(deposit.received_amount) - _money(deposit.refunded_amount) - _money(deposit.offset_amount))
    if amount > available:
        raise HTTPException(400, "退还金额不得超过可退押金")
    deposit.customer_id = contract.customer_id
    deposit.refunded_amount = _money(_money(deposit.refunded_amount) + amount)
    deposit.refund_date = payload.action_date or today_in_timezone()
    deposit.notes = _append_note(deposit.notes, payload.notes)
    deposit.updated_at = now_in_timezone()
    _sync_status(deposit)
    log_action(
        db,
        contract.id,
        "deposit_refund",
        dump_changes({"deposit_id": deposit.id, "amount": amount, "status": deposit.status}),
        operator,
        commit=False,
        target_type="deposit",
        target_id=deposit.id,
        object_scope=_contract_log_scope(contract),
    )
    db.commit()
    db.refresh(deposit)
    return serialize_deposit(deposit)


def offset_deposit(db: Session, contract: Contract, deposit: ContractDeposit, payload: DepositActionPayload, operator: str) -> dict:
    _guard_finance_link(db, deposit)
    amount = _money(payload.amount)
    if amount <= 0:
        raise HTTPException(400, "押金冲抵金额必须大于 0")
    available = _money(_money(deposit.received_amount) - _money(deposit.refunded_amount) - _money(deposit.offset_amount))
    if amount > available:
        raise HTTPException(400, "冲抵金额不得超过已收未退未冲抵押金")
    deposit.customer_id = contract.customer_id
    deposit.offset_amount = _money(_money(deposit.offset_amount) + amount)
    deposit.notes = _append_note(deposit.notes, payload.notes)
    deposit.updated_at = now_in_timezone()
    _sync_status(deposit)
    log_action(
        db,
        contract.id,
        "deposit_offset",
        dump_changes({"deposit_id": deposit.id, "amount": amount, "status": deposit.status}),
        operator,
        commit=False,
        target_type="deposit",
        target_id=deposit.id,
        object_scope=_contract_log_scope(contract),
    )
    db.commit()
    db.refresh(deposit)
    return serialize_deposit(deposit)
