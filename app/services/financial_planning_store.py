from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.money import round_money
from app.core.time import now_in_timezone
from app.financial_planning_schemas import ExpensePayload, ForecastRequest, SaveVersionRequest
from app.models import Contract, FinancialExpense, FinancialPlanVersion, FinancialPlanningState, User
from app.services.common import log_action
from app.services.legacy_demo_a_scope import ObjectScope


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def expense_dict(row: FinancialExpense) -> dict:
    result = {key: getattr(row, key) for key in (
        "id", "building_id", "name", "category", "amount_yuan", "occurrence_month",
        "payment_month", "owner", "notes", "is_active", "revision", "created_by", "updated_by",
    )}
    result["paid_on"] = row.paid_on.isoformat() if row.paid_on else None
    result["created_at"] = row.created_at.isoformat()
    result["updated_at"] = row.updated_at.isoformat()
    return result


def all_expenses(db: Session, building_id: int) -> list[dict]:
    from app.services.finance_operations import enrich_expenses
    items = [expense_dict(row) for row in db.query(FinancialExpense).filter(
        FinancialExpense.building_id == building_id, FinancialExpense.is_active.is_(True)
    ).order_by(FinancialExpense.occurrence_month, FinancialExpense.id).all()]
    return enrich_expenses(db, building_id, items)



def expenses_for_year(db: Session, building_id: int, year: int) -> list[dict]:
    prefix = str(year)
    return [row for row in all_expenses(db, building_id) if (
        (row.get("detail") and row["detail"]["service_from"] <= f"{year}-12" and row["detail"]["service_to"] >= f"{year}-01")
        or any(p["paid_on"].startswith(prefix) for p in row.get("payments", []))
        or row["occurrence_month"].startswith(prefix) or row["payment_month"].startswith(prefix)
        or (row["paid_on"] or "").startswith(prefix)
        or (not row["paid_on"] and row["payment_month"] < f"{year}-01")
    )]


def planning_state(db: Session, building_id: int, year: int):
    return db.query(FinancialPlanningState).filter_by(building_id=building_id, year=year).one_or_none()


def get_version(db: Session, version_id: int, building_id: int, year: int | None = None):
    query = db.query(FinancialPlanVersion).filter_by(id=version_id, building_id=building_id)
    if year is not None:
        query = query.filter_by(year=year)
    row = query.one_or_none()
    if row is None:
        raise HTTPException(404, "方案不存在或不属于当前项目与年度")
    return row


def version_dict(row: FinancialPlanVersion, *, summary: bool = False) -> dict:
    snapshot = json.loads(row.snapshot_json)
    result = {key: getattr(row, key) for key in (
        "id", "building_id", "year", "revision", "kind", "name", "base_version_id",
        "closed_through_month", "source_digest", "created_by",
    )}
    result["created_at"] = row.created_at.isoformat()
    result["totals"] = snapshot.get("totals", {})
    result["budget_version_id"] = snapshot.get("budget_version_id")
    if not summary:
        result["snapshot"] = snapshot
        result["client_state"] = json.loads(row.client_state_json)
        result["contract_adjustments"] = snapshot.get("contract_adjustments", [])
    return result


def list_versions(db: Session, building_id: int, year: int) -> list[dict]:
    # Project immutable summaries in SQLite; do not transfer and deserialize every
    # full source snapshot/client draft just to populate the version picker.
    model = FinancialPlanVersion
    fields = ("id", "building_id", "year", "revision", "kind", "name", "base_version_id",
              "closed_through_month", "source_digest", "created_by", "created_at")
    rows = db.query(
        *(getattr(model, key) for key in fields),
        func.json_extract(model.snapshot_json, "$.totals").label("totals_json"),
        func.json_type(model.snapshot_json, "$.totals").label("totals_type"),
        func.json_extract(model.snapshot_json, "$.budget_version_id").label("budget_version_id"),
    ).filter(model.building_id == building_id, model.year == year).order_by(model.revision.desc()).all()
    result = []
    for row in rows:
        item = {key: getattr(row, key) for key in fields}
        item["created_at"] = row.created_at.isoformat()
        item["totals"] = json.loads(row.totals_json) if row.totals_json is not None else (
            None if row.totals_type == "null" else {}
        )
        item["budget_version_id"] = row.budget_version_id
        result.append(item)
    return result


def build_forecast(db: Session, building_id: int, payload: ForecastRequest, *, _period_month: str | None = None) -> dict:
    from app.services.financial_planning import build_financial_forecast

    adjustments = [item.model_dump() for item in payload.contract_adjustments]
    adjustment_ids = {item["contract_id"] for item in adjustments}
    if adjustment_ids:
        found = {int(row[0]) for row in db.query(Contract.id).filter(
            Contract.id.in_(adjustment_ids), Contract.building_id == building_id,
            Contract.is_deleted.is_(False),
        ).all()}
        if found != adjustment_ids:
            raise HTTPException(404, "调整合同不存在或不属于当前项目")
    frozen = None
    if payload.base_version_id is not None:
        base = get_version(db, payload.base_version_id, building_id, payload.year)
        if base.kind == "budget":
            raise HTTPException(422, "已发生期间应沿用预测版本，不能将预算作为实际基准")
        frozen = json.loads(base.snapshot_json)
    try:
        result = build_financial_forecast(
            db, building_id=building_id, year=payload.year,
            closed_through_month=payload.closed_through_month,
            expenses=all_expenses(db, building_id), contract_adjustments=adjustments,
            frozen_snapshot=frozen,
            _period_month=_period_month,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    result["contract_adjustments"] = adjustments
    result["base_version_id"] = payload.base_version_id
    return result


def _budget_snapshot(forecast: dict, payload: SaveVersionRequest) -> dict:
    def fen(value):
        return int((Decimal(str(value)) * 1_000_000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    snapshot = deepcopy(forecast)
    budget_rows = {row.month: row for row in payload.budget_months or []}
    differences = []
    for month in snapshot["months"]:
        row = budget_rows[int(month["month"])]
        new_values = {"revenue": fen(row.revenue), "operating_cost": fen(row.operating_cost)}
        new_values["ebitda"] = new_values["revenue"] - new_values["operating_cost"]
        change = {"month": int(month["month"])}
        for key, value in new_values.items():
            change[key] = (value - fen(month.get(key, 0))) / 1_000_000
            month[key] = value / 1_000_000
        differences.append(change)
    for key in ("revenue", "operating_cost", "ebitda"):
        snapshot["totals"][key] = sum(fen(row[key]) for row in snapshot["months"]) / 1_000_000
    if any(row[key] != 0 for row in differences for key in ("revenue", "operating_cost")):
        snapshot["sources"].append({
            "source_key": "budget_adjustment", "source_type": "budget_adjustment",
            "source_id": None, "label": "预算编制调整", "months": differences,
            "source_snapshot": {"budget_months": [row.model_dump() for row in payload.budget_months]},
        })
    snapshot["budget_basis"] = "management_profit_and_loss"
    return snapshot


def _reserve_revision(db: Session, building_id: int, year: int, expected: int):
    state = planning_state(db, building_id, year)
    if state is None:
        if expected != 0:
            raise HTTPException(409, "方案版本已变化，请刷新后保存")
        state = FinancialPlanningState(building_id=building_id, year=year, revision=1)
        db.add(state)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, "方案版本已变化，请刷新后保存") from exc
        return state
    updated = db.query(FinancialPlanningState).filter_by(id=state.id, revision=expected).update(
        {"revision": expected + 1, "updated_at": now_in_timezone()}, synchronize_session=False,
    )
    if updated != 1:
        db.rollback()
        raise HTTPException(409, "方案版本已变化，请刷新后保存")
    db.refresh(state)
    return state


def _log(db: Session, building_id: int, user: User, action: str, target: str, target_id: int, detail: str):
    log_action(db, None, action, detail, user.username, commit=False,
               target_type=target, target_id=target_id,
               object_scope=ObjectScope(scope_type="building", building_id=building_id))


def save_version(db: Session, building_id: int, user: User, payload: SaveVersionRequest, *, adopt: bool = True) -> dict:
    from app.services.strategy_reference import resolved_strategy_state
    client_state = resolved_strategy_state(db, building_id, payload.client_state)
    state_before = planning_state(db, building_id, payload.year)
    budget_id = payload.budget_version_id
    if payload.kind == "budget" and budget_id is not None:
        raise HTTPException(422, "预算版本不应关联另一份预算作为方案基准")
    if payload.kind != "budget" and budget_id is None and state_before:
        budget_id = state_before.active_budget_id
    if budget_id is not None:
        budget = get_version(db, budget_id, building_id, payload.year)
        if budget.kind != "budget":
            raise HTTPException(422, "方案的预算基准必须为预算版本")
    source = build_forecast(db, building_id, payload)
    source_digest = hashlib.sha256(canonical_json({
        "year": source["year"], "sources": source["sources"],
        "closed_through_month": source["closed_through_month"],
    }).encode("utf-8")).hexdigest()
    snapshot = _budget_snapshot(source, payload) if payload.kind == "budget" else source
    snapshot["budget_version_id"] = budget_id
    state = _reserve_revision(db, building_id, payload.year, payload.expected_revision)
    version = FinancialPlanVersion(
        building_id=building_id, year=payload.year, revision=state.revision,
        kind=payload.kind, name=payload.name, base_version_id=payload.base_version_id,
        closed_through_month=payload.closed_through_month,
        client_state_json=canonical_json(client_state), snapshot_json=canonical_json(snapshot),
        source_digest=source_digest, created_by=user.username,
    )
    db.add(version)
    db.flush()
    if adopt and payload.kind == "budget":
        state.active_budget_id = version.id
    elif adopt and payload.kind == "forecast":
        state.active_forecast_id = version.id
    _log(db, building_id, user, "financial_plan_save", "financial_plan", version.id,
         f"保存{payload.year}年{payload.kind}版本{version.revision}：{version.name}；来源摘要{source_digest[:12]}")
    response = {"version": version_dict(version), "revision": state.revision}
    db.commit()
    return response


def adopt_version(db: Session, building_id: int, user: User, version_id: int, expected_revision: int) -> dict:
    version = get_version(db, version_id, building_id)
    state = _reserve_revision(db, building_id, version.year, expected_revision)
    if version.kind == "budget":
        state.active_budget_id = version.id
    else:
        state.active_forecast_id = version.id
    _log(db, building_id, user, "financial_plan_adopt", "financial_plan", version.id,
         f"采用{version.year}年版本{version.revision}：{version.name}")
    response = {"version": version_dict(version), "revision": state.revision}
    db.commit()
    return response


def save_expense(db: Session, building_id: int, user: User, payload: ExpensePayload,
                 expense_id: int | None = None) -> dict:
    fields = payload.model_dump(exclude={"expected_revision"})
    fields["amount_yuan"] = round_money(fields["amount_yuan"])
    if expense_id is None:
        row = FinancialExpense(building_id=building_id, created_by=user.username,
                               updated_by=user.username, revision=1, **fields)
        db.add(row)
        db.flush()
    else:
        row = db.query(FinancialExpense).filter_by(id=expense_id, building_id=building_id).one_or_none()
        if row is None:
            raise HTTPException(404, "费用不存在或不属于当前项目")
        if payload.expected_revision is None:
            raise HTTPException(422, "更新费用需要当前版本号")
        updated = db.query(FinancialExpense).filter_by(id=row.id, revision=payload.expected_revision).update(
            {**fields, "revision": payload.expected_revision + 1, "updated_by": user.username,
             "updated_at": now_in_timezone()}, synchronize_session=False,
        )
        if updated != 1:
            db.rollback()
            raise HTTPException(409, "费用已被更新，请刷新后重试")
        db.refresh(row)
    _log(db, building_id, user, "financial_expense_save", "financial_expense", row.id,
         f"{'新增' if expense_id is None else '更新'}费用：{row.name}；版本{row.revision}；{row.category}")
    response = expense_dict(row)
    db.commit()
    return response
