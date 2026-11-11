from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import json
import re

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import (
    Building,
    Contract,
    ContractRoom,
    FinancialExpense,
    FinancialPlanVersion,
    FinancialPlanningState,
    OperationLog,
    Room,
    Zone,
)
from app.services.contract_rent import calculate_monthly_rent
from app.services import financial_planning_store as planning_store


PREFIX = "/api/financial-planning"
SCOPE = {"building": "DEMO_A"}


@pytest.fixture
def year() -> int:
    # A future year keeps planned cash assertions independent of today's month.
    return today_in_timezone().year + 1


@pytest.fixture
def admin():
    with TestClient(app) as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
        )
        assert registered.status_code == 200, registered.text
        _login(client, "admin", "Admin123")
        yield client


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


def _source(client: TestClient, year: int, closed: int = 0) -> dict:
    response = client.get(
        f"{PREFIX}/source",
        params={**SCOPE, "year": year, "closed_through_month": closed},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _expense_payload(year: int, **changes) -> dict:
    return {
        "name": "物业运营费用",
        "category": "opex",
        "amount_yuan": 20000,
        "occurrence_month": f"{year}-02",
        "payment_month": f"{year}-03",
        "paid_on": None,
        "owner": "运营负责人",
        "notes": "验收测试费用",
        **changes,
    }


def _expense(client: TestClient, year: int, **changes) -> dict:
    response = client.post(f"{PREFIX}/expenses", params=SCOPE, json=_expense_payload(year, **changes))
    assert response.status_code == 200, response.text
    return response.json()


def _budget_months(snapshot: dict) -> list[dict]:
    return [
        {"month": row["month"], "revenue": row["revenue"], "operating_cost": row["operating_cost"]}
        for row in snapshot["months"]
    ]


def _version_payload(client: TestClient, year: int, kind: str = "forecast", **changes) -> dict:
    source = _source(client, year)
    payload = {
        "year": year,
        "name": f"年度{kind}方案",
        "kind": kind,
        "closed_through_month": 0,
        "expected_revision": source["revision"],
        "client_state": {},
    }
    if kind == "budget":
        payload["budget_months"] = _budget_months(source["forecast"])
    return {**payload, **changes}


def _save(client: TestClient, year: int, kind: str = "forecast", **changes) -> dict:
    response = client.post(
        f"{PREFIX}/versions", params=SCOPE, json=_version_payload(client, year, kind, **changes)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _seed_contract(year: int, *, code: str = "DEMO_A", suffix: str = "MAIN", free_period: int = 0) -> int:
    with SessionLocal() as db:
        building = db.query(Building).filter(Building.code == code).one()
        zone = db.query(Zone).filter(Zone.building_id == building.id).order_by(Zone.id).first()
        if zone is None:
            zone = Zone(
                building_id=building.id, code="OFFICE", name="测试办公分区", zone_type="office",
                valid_from=today_in_timezone(), is_active=True,
            )
            db.add(zone)
            db.flush()
        room = Room(
            building_id=building.id,
            zone_id=zone.id,
            tower="南塔",
            floor="9",
            room_no=f"FP-{suffix}",
            room_type="办公",
            area=100,
            is_leasable=True,
            is_deleted=False,
        )
        db.add(room)
        db.flush()
        monthly_rent = calculate_monthly_rent(100, 4)
        contract = Contract(
            building_id=building.id,
            contract_no=f"FP-{suffix}",
            tower="南塔",
            room_no=room.room_no,
            tenant_name=f"业财测试客户-{suffix}",
            area=100,
            unit_price=4,
            monthly_rent=monthly_rent,
            free_period=free_period,
            start_date=date(year, 1, 1),
            end_date=date(year, 12, 31),
            contact_phone="13900001111",
            contact_email="private@example.invalid",
            is_deleted=False,
        )
        db.add(contract)
        db.flush()
        db.add(ContractRoom(
            building_id=building.id,
            contract_id=contract.id,
            room_id=room.id,
            tower="南塔",
            room_no=room.room_no,
            area=100,
            unit_price=4,
            monthly_rent=monthly_rent,
            start_date=contract.start_date,
            end_date=contract.end_date,
        ))
        db.commit()
        return int(contract.id)


def _change_contract_rent(contract_id: int, unit_price: float = 5) -> None:
    with SessionLocal() as db:
        contract = db.get(Contract, contract_id)
        contract.unit_price = unit_price
        contract.monthly_rent = calculate_monthly_rent(contract.area, unit_price)
        for room in contract.room_items:
            room.unit_price = unit_price
            room.monthly_rent = calculate_monthly_rent(room.area, unit_price)
        db.commit()


def test_source_builds_twelve_months_from_contract_and_excludes_private_fields(admin, year) -> None:
    contract_id = _seed_contract(year)
    response = _source(admin, year)
    assert response["building"]["code"] == "DEMO_A"
    assert response["revision"] == 0
    assert response["can_write"] is True
    assert response["approved_budget"] is None
    assert response["latest_forecast"] is None
    assert response["versions"] == []
    forecast = response["forecast"]
    assert forecast["year"] == year
    assert forecast["unit"] == "万元"
    assert forecast["currency"] == "CNY"
    assert [row["month"] for row in forecast["months"]] == list(range(1, 13))
    assert forecast["totals"]["revenue"] > 0
    assert forecast["totals"]["revenue"] == pytest.approx(
        sum(row["revenue"] for row in forecast["months"]), abs=0.00001
    )
    contract_sources = [row for row in forecast["sources"] if row["source_type"] == "contract"]
    assert len(contract_sources) == 1
    assert contract_sources[0]["contract_id"] == contract_id
    assert contract_sources[0]["source_id"] == contract_id
    assert contract_sources[0]["source_snapshot"]
    serialized = json.dumps(forecast, ensure_ascii=False)
    assert "13900001111" not in serialized
    assert "private@example.invalid" not in serialized


def test_source_workspace_fallback_matches_live_and_none_skips_computation(admin, year, monkeypatch) -> None:
    _seed_contract(year)
    _expense(admin, year)
    budget = _save(admin, year, "budget")["version"]
    default = _source(admin, year)
    assert default["latest_forecast"] is None
    for mode in ("live", "workspace"):
        response = admin.get(f"{PREFIX}/source", params={**SCOPE, "year": year, "forecast_mode": mode})
        assert response.status_code == 200, response.text
        result = response.json()
        result["forecast"]["generated_at"] = default["forecast"]["generated_at"]
        assert result == default
        assert result["approved_budget"] == budget

    def unexpected_forecast(*args, **kwargs):
        pytest.fail("metadata-only source must not build a forecast")

    monkeypatch.setattr(planning_store, "build_forecast", unexpected_forecast)
    response = admin.get(f"{PREFIX}/source", params={**SCOPE, "year": year, "forecast_mode": "none"})
    assert response.status_code == 200, response.text
    assert response.json() == {**default, "forecast": None}
    invalid = admin.get(f"{PREFIX}/source", params={**SCOPE, "year": year, "forecast_mode": "unknown"})
    assert invalid.status_code == 422


@pytest.mark.parametrize("kind", ["forecast", "scenario"])
def test_source_workspace_keeps_complete_saved_versions_without_recalculation(admin, year, monkeypatch, kind) -> None:
    contract_id = _seed_contract(year)
    _expense(admin, year)
    budget = _save(admin, year, "budget")["version"]
    saved = _save(admin, year, kind, client_state={"strategy": {"mode": "hold"}})["version"]
    if kind == "scenario":
        adopted = admin.post(f"{PREFIX}/versions/{saved['id']}/adopt", params=SCOPE,
                             json={"expected_revision": saved["revision"]})
        assert adopted.status_code == 200, adopted.text
    _change_contract_rent(contract_id, 6)
    default = _source(admin, year)
    assert default["forecast"]["totals"] != saved["snapshot"]["totals"]
    assert default["forecast"]["base_version_id"] == saved["id"]
    assert default["approved_budget"] == budget
    assert default["latest_forecast"] == saved

    def unexpected_forecast(*args, **kwargs):
        pytest.fail("saved workspace and metadata-only source must not build a forecast")

    monkeypatch.setattr(planning_store, "build_forecast", unexpected_forecast)
    for mode in ("workspace", "workspace_deferred", "none"):
        response = admin.get(f"{PREFIX}/source", params={**SCOPE, "year": year, "forecast_mode": mode})
        assert response.status_code == 200, response.text
        assert response.json() == {**default, "forecast": None}


@pytest.mark.parametrize("has_budget", [False, True])
@pytest.mark.parametrize("saved_kind", [None, "forecast", "scenario"])
def test_deferred_workspace_builds_only_when_neither_budget_nor_saved_forecast_exists(
    admin, year, monkeypatch, has_budget, saved_kind,
) -> None:
    _seed_contract(year)
    _expense(admin, year)
    budget = _save(admin, year, "budget")["version"] if has_budget else None
    saved = _save(admin, year, saved_kind)["version"] if saved_kind else None
    if saved_kind == "scenario":
        response = admin.post(f"{PREFIX}/versions/{saved['id']}/adopt", params=SCOPE,
                              json={"expected_revision": saved["revision"]})
        assert response.status_code == 200, response.text
    default = _source(admin, year)
    build_forecast = planning_store.build_forecast
    calls = []

    def counted_forecast(db, building_id, payload):
        calls.append(payload.model_dump())
        return build_forecast(db, building_id, payload)

    monkeypatch.setattr(planning_store, "build_forecast", counted_forecast)
    response = admin.get(f"{PREFIX}/source", params={**SCOPE, "year": year, "forecast_mode": "workspace_deferred"})
    assert response.status_code == 200, response.text
    result = response.json()
    should_build = not budget and not saved
    assert len(calls) == int(should_build)
    if should_build:
        result["forecast"]["generated_at"] = default["forecast"]["generated_at"]
    assert result == (default if should_build else {**default, "forecast": None})
    assert result["approved_budget"] == budget
    assert result["latest_forecast"] == saved
    if budget and not saved:
        compared = admin.post(f"{PREFIX}/compare", params={**SCOPE, "include_baseline": False},
                              json={"year": year, "baseline_version_id": budget["id"]})
        assert compared.status_code == 200, compared.text
        current = compared.json()["current"]
        current["generated_at"] = default["forecast"]["generated_at"]
        assert current == default["forecast"]
        assert len(calls) == 1
        assert compared.json()["comparison"]["current_total"] == current["totals"]["ebitda"]
    with SessionLocal() as db:
        assert db.query(FinancialPlanVersion).count() == int(has_budget) + int(saved_kind is not None)


@pytest.mark.parametrize("mode", ["live", "workspace", "workspace_deferred", "none"])
@pytest.mark.parametrize("kind", ["forecast", "scenario"])
def test_source_can_omit_latest_snapshot_without_trimming_budget_or_saved_version_detail(admin, year, mode, kind) -> None:
    contract_id = _seed_contract(year)
    _expense(admin, year)
    budget = _save(admin, year, "budget", client_state={"strategy": {"mode": "hold"}})["version"]
    saved = _save(admin, year, kind, client_state={"strategy": {"mode": "sell"}})["version"]
    if kind == "scenario":
        response = admin.post(f"{PREFIX}/versions/{saved['id']}/adopt", params=SCOPE,
                              json={"expected_revision": saved["revision"]})
        assert response.status_code == 200, response.text
    _change_contract_rent(contract_id, 5)
    params = {**SCOPE, "year": year, "forecast_mode": mode}
    default = admin.get(f"{PREFIX}/source", params=params)
    assert default.status_code == 200, default.text
    full = default.json()
    with SessionLocal() as db:
        audit_count = db.query(OperationLog).count()
    for include in (True, False):
        response = admin.get(f"{PREFIX}/source", params={**params, "include_latest_snapshot": include})
        assert response.status_code == 200, response.text
        result = response.json()
        if result["forecast"]:
            result["forecast"]["generated_at"] = full["forecast"]["generated_at"]
        expected_latest = saved if include else {
            key: value for key, value in saved.items() if key not in {"snapshot", "client_state", "contract_adjustments"}
        }
        assert result == {**full, "latest_forecast": expected_latest}
        assert result["approved_budget"] == budget
        assert result["latest_forecast"]["id"] == saved["id"]
        assert result["latest_forecast"]["budget_version_id"] == budget["id"]
    detail = admin.get(f"{PREFIX}/versions/{saved['id']}", params=SCOPE)
    assert detail.status_code == 200, detail.text
    assert detail.json() == saved
    with SessionLocal() as db:
        assert db.query(OperationLog).count() == audit_count
        assert db.query(FinancialPlanVersion).count() == 2


@pytest.mark.parametrize("current_kind", ["live", "forecast", "scenario"])
def test_compare_can_omit_baseline_without_changing_current_or_audit_details(admin, year, current_kind) -> None:
    contract_id = _seed_contract(year)
    _expense(admin, year)
    baseline = _save(admin, year, "budget")["version"]
    _change_contract_rent(contract_id, 5)
    saved = _save(admin, year, current_kind)["version"] if current_kind != "live" else None
    _change_contract_rent(contract_id, 6)
    with SessionLocal() as db:
        audit_count = db.query(OperationLog).count()
    for metric in ("ebitda", "revenue", "operating_cost", "net_cash_flow"):
        payload = {"year": year, "baseline_version_id": baseline["id"], "metric": metric}
        if saved:
            payload["current_version_id"] = saved["id"]
        default = admin.post(f"{PREFIX}/compare", params=SCOPE, json=payload)
        assert default.status_code == 200, default.text
        full = default.json()
        assert full["baseline"] == baseline
        if saved:
            assert full["current"] == saved["snapshot"]
        for include in (True, False):
            response = admin.post(f"{PREFIX}/compare", params={**SCOPE, "include_baseline": include}, json=payload)
            assert response.status_code == 200, response.text
            result = response.json()
            if not saved:
                result["current"]["generated_at"] = full["current"]["generated_at"]
            expected = full if include else {key: value for key, value in full.items() if key != "baseline"}
            assert result == expected
            assert result["current"]["sources"]
            assert sum(row["delta"] for row in result["comparison"]["items"]) == pytest.approx(
                result["comparison"]["delta"], abs=0.00001)
    detail = admin.get(f"{PREFIX}/versions/{baseline['id']}", params=SCOPE)
    assert detail.json() == baseline
    with SessionLocal() as db:
        assert db.query(OperationLog).count() == audit_count


def test_expense_crud_revisions_retire_instead_of_delete_and_audit(admin, year) -> None:
    expense = _expense(admin, year)
    assert expense["revision"] == 1
    assert expense["amount_yuan"] == 20000
    assert expense["paid_on"] is None
    assert expense["created_by"] == "admin"
    update = _expense_payload(year, amount_yuan=30000, paid_on=f"{year}-03-12", expected_revision=1)
    response = admin.put(f"{PREFIX}/expenses/{expense['id']}", params=SCOPE, json=update)
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2
    assert response.json()["paid_on"] == f"{year}-03-12"
    conflict = admin.put(f"{PREFIX}/expenses/{expense['id']}", params=SCOPE, json=update)
    assert conflict.status_code == 409
    missing_revision = admin.put(
        f"{PREFIX}/expenses/{expense['id']}", params=SCOPE, json=_expense_payload(year)
    )
    assert missing_revision.status_code == 422
    retired = admin.put(
        f"{PREFIX}/expenses/{expense['id']}",
        params=SCOPE,
        json={**update, "expected_revision": 2, "is_active": False},
    )
    assert retired.status_code == 200, retired.text
    assert retired.json()["revision"] == 3
    listed = admin.get(f"{PREFIX}/expenses", params={**SCOPE, "year": year})
    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert admin.delete(f"{PREFIX}/expenses/{expense['id']}", params=SCOPE).status_code == 405
    with SessionLocal() as db:
        row = db.get(FinancialExpense, expense["id"])
        assert row.is_active is False
        assert row.amount_yuan == 30000
        assert db.query(OperationLog).filter(
            OperationLog.action == "financial_expense_save",
            OperationLog.target_type == "financial_expense",
            OperationLog.target_id == expense["id"],
        ).count() == 3


@pytest.mark.parametrize("field,value", [
    ("amount_yuan", None), ("amount_yuan", ""), ("amount_yuan", True), ("amount_yuan", -1),
    ("amount_yuan", "NaN"), ("amount_yuan", "Infinity"), ("name", "   "),
    ("category", "unknown"), ("occurrence_month", "2027-13"),
    ("payment_month", "1999-12"), ("paid_on", "not-a-date"),
])
def test_expense_invalid_values_do_not_write(admin, year, field, value) -> None:
    response = admin.post(
        f"{PREFIX}/expenses", params=SCOPE, json=_expense_payload(year, **{field: value})
    )
    assert response.status_code == 422, response.text
    with SessionLocal() as db:
        assert db.query(FinancialExpense).count() == 0


def test_expense_zero_is_valid_and_cross_year_listing_keeps_unpaid_arrears(admin, year) -> None:
    zero = _expense(admin, year, amount_yuan=0)
    carried = _expense(admin, year, name="上年发生本年支付", occurrence_month=f"{year - 1}-12")
    paid_in_year = _expense(
        admin, year, name="跨年实际支付", occurrence_month=f"{year - 1}-10",
        payment_month=f"{year - 1}-11", paid_on=f"{year}-01-10",
    )
    arrears = _expense(
        admin, year, name="未付往年欠款", occurrence_month=f"{year - 1}-09",
        payment_month=f"{year - 1}-10",
    )
    _expense(
        admin, year, name="已付往年费用", occurrence_month=f"{year - 1}-09",
        payment_month=f"{year - 1}-10", paid_on=f"{year - 1}-10-10",
    )
    _expense(admin, year, name="次年费用", occurrence_month=f"{year + 1}-01", payment_month=f"{year + 1}-02")
    response = admin.get(f"{PREFIX}/expenses", params={**SCOPE, "year": year})
    assert response.status_code == 200
    assert {row["id"] for row in response.json()["items"]} == {
        zero["id"], carried["id"], paid_in_year["id"], arrears["id"]
    }


def test_opex_and_capex_use_different_profit_and_cash_months(admin, year) -> None:
    _expense(admin, year, amount_yuan=20000)
    _expense(
        admin, year, name="改造资本支出", category="capex", amount_yuan=50000,
        occurrence_month=f"{year}-02", payment_month=f"{year}-04",
    )
    forecast = _source(admin, year)["forecast"]
    rows = {row["month"]: row for row in forecast["months"]}
    assert rows[2]["operating_cost"] == pytest.approx(2)
    assert rows[2]["ebitda"] == pytest.approx(-2)
    assert rows[3]["net_cash_flow"] == pytest.approx(-2)
    assert rows[4]["net_cash_flow"] == pytest.approx(-5)
    assert forecast["totals"]["ebitda"] == pytest.approx(-2)
    assert forecast["totals"]["net_cash_flow"] == pytest.approx(-7)


def test_budget_manual_amounts_are_traceable_adjustments(admin, year) -> None:
    contract_id = _seed_contract(year)
    original = _source(admin, year)["forecast"]
    months = _budget_months(original)
    months[0]["revenue"] += 2
    months[0]["operating_cost"] += 1
    saved = _save(admin, year, "budget", budget_months=months)
    version = saved["version"]
    assert saved["revision"] == 1
    assert re.fullmatch(r"[a-f0-9]{64}", version["source_digest"])
    snapshot = version["snapshot"]
    assert snapshot["months"][0]["revenue"] == months[0]["revenue"]
    assert snapshot["months"][0]["operating_cost"] == months[0]["operating_cost"]
    original_contract = next(row for row in original["sources"] if row["source_id"] == contract_id)
    saved_contract = next(row for row in snapshot["sources"] if row["source_type"] == "contract")
    assert saved_contract == original_contract
    adjustments = [row for row in snapshot["sources"] if row["source_type"] == "budget_adjustment"]
    assert adjustments
    assert sum(row["months"][0]["ebitda"] for row in adjustments) == pytest.approx(1)
    response = admin.post(
        f"{PREFIX}/compare", params=SCOPE,
        json={"baseline_version_id": version["id"], "year": year},
    )
    assert response.status_code == 200, response.text
    comparison = response.json()["comparison"]
    assert comparison["delta"] == pytest.approx(-1)
    assert sum(row["delta"] for row in comparison["items"]) == pytest.approx(-1)
    assert any(row["source_type"] == "budget_adjustment" and row["delta"] == pytest.approx(-1)
               for row in comparison["items"])


@pytest.mark.parametrize("invalid", ["missing", "eleven", "duplicate", "null", "blank", "negative"])
def test_budget_requires_complete_nonnegative_twelve_months(admin, year, invalid) -> None:
    payload = _version_payload(admin, year, "budget")
    if invalid == "missing":
        payload.pop("budget_months")
    elif invalid == "eleven":
        payload["budget_months"].pop()
    elif invalid == "duplicate":
        payload["budget_months"][-1]["month"] = 1
    else:
        payload["budget_months"][0]["revenue"] = {"null": None, "blank": "", "negative": -1}[invalid]
    response = admin.post(f"{PREFIX}/versions", params=SCOPE, json=payload)
    assert response.status_code == 422, response.text
    assert _source(admin, year)["revision"] == 0


def test_versions_are_append_only_and_budget_survives_contract_expense_changes(admin, year) -> None:
    contract_id = _seed_contract(year)
    expense = _expense(admin, year)
    first = _save(admin, year, "budget")["version"]
    frozen = deepcopy(first)
    _change_contract_rent(contract_id)
    updated = admin.put(
        f"{PREFIX}/expenses/{expense['id']}", params=SCOPE,
        json=_expense_payload(year, amount_yuan=40000, expected_revision=1),
    )
    assert updated.status_code == 200
    second = _save(admin, year, "forecast")["version"]
    assert second["id"] != first["id"]
    assert second["revision"] > first["revision"]
    assert second["snapshot"]["totals"] != first["snapshot"]["totals"]
    detail = admin.get(f"{PREFIX}/versions/{first['id']}", params=SCOPE)
    assert detail.status_code == 200
    assert detail.json() == frozen
    source = _source(admin, year)
    assert source["approved_budget"] == frozen
    assert source["latest_forecast"]["id"] == second["id"]
    versions = admin.get(f"{PREFIX}/versions", params={**SCOPE, "year": year})
    assert versions.status_code == 200
    assert {row["id"] for row in versions.json()["items"]} == {first["id"], second["id"]}
    assert all("snapshot" not in row and "client_state" not in row for row in versions.json()["items"])
    for method in ("PUT", "PATCH", "DELETE"):
        assert admin.request(method, f"{PREFIX}/versions/{first['id']}", params=SCOPE, json={}).status_code == 405
    with SessionLocal() as db:
        assert db.query(FinancialPlanVersion).count() == 2


def test_saved_client_state_including_asset_strategy_survives_new_login(admin, year) -> None:
    state = {
        "scenario": {"name": "续租改善", "annualTarget": 1200, "actions": [{"id": "a1", "month": 4}]},
        "strategy": {"mode": "renovate", "capex": 300, "exitYear": 5, "discountRate": 0.08},
        "owners": {"a1": "招商主管"},
        "comparison": {"selectedScenarioIds": ["base", "improve"]},
    }
    saved = _save(admin, year, "scenario", client_state=state)["version"]
    adopted = admin.post(
        f"{PREFIX}/versions/{saved['id']}/adopt", params=SCOPE,
        json={"expected_revision": _source(admin, year)["revision"]},
    )
    assert adopted.status_code == 200, adopted.text
    admin.post("/api/auth/logout")
    with TestClient(app) as fresh:
        _login(fresh, "admin", "Admin123")
        detail = fresh.get(f"{PREFIX}/versions/{saved['id']}", params=SCOPE)
        assert detail.status_code == 200
        assert detail.json()["client_state"] == state
        latest = _source(fresh, year)["latest_forecast"]
        assert latest["id"] == saved["id"]
        assert latest["kind"] == "scenario"
        assert latest["client_state"] == state


def test_compare_contract_and_expense_details_add_up_for_each_supported_metric(admin, year) -> None:
    contract_id = _seed_contract(year)
    expense = _expense(admin, year)
    baseline = _save(admin, year, "budget")["version"]
    _change_contract_rent(contract_id)
    response = admin.put(
        f"{PREFIX}/expenses/{expense['id']}", params=SCOPE,
        json=_expense_payload(year, amount_yuan=40000, expected_revision=1),
    )
    assert response.status_code == 200
    for metric in ("ebitda", "revenue", "operating_cost", "net_cash_flow"):
        response = admin.post(
            f"{PREFIX}/compare", params=SCOPE,
            json={"baseline_version_id": baseline["id"], "year": year, "metric": metric},
        )
        assert response.status_code == 200, response.text
        comparison = response.json()["comparison"]
        assert comparison["metric"] == metric
        assert comparison["delta"] == pytest.approx(
            comparison["current_total"] - comparison["baseline_total"], abs=0.00001
        )
        assert sum(row["delta"] for row in comparison["items"]) == pytest.approx(comparison["delta"], abs=0.00001)
        if metric == "ebitda":
            assert {(row["source_type"], row["source_id"]) for row in comparison["items"]} >= {
                ("contract", contract_id), ("expense", expense["id"])
            }
        for row in comparison["items"]:
            assert row["source_key"] and row["label"] and row["reason"]
            assert row["delta"] == pytest.approx(row["current_value"] - row["baseline_value"], abs=0.00001)
            assert sum(month["delta"] for month in row["months"]) == pytest.approx(row["delta"], abs=0.00001)


def test_conflicting_save_and_adopt_do_not_overwrite_history(admin, year) -> None:
    original_payload = _version_payload(admin, year, "forecast")
    first = admin.post(f"{PREFIX}/versions", params=SCOPE, json=original_payload)
    assert first.status_code == 200
    original = first.json()["version"]
    stale = admin.post(f"{PREFIX}/versions", params=SCOPE, json={**original_payload, "name": "过期页面的保存"})
    assert stale.status_code == 409
    second = _save(admin, year, "forecast", name="新预测")["version"]
    stale_adopt = admin.post(
        f"{PREFIX}/versions/{original['id']}/adopt", params=SCOPE, json={"expected_revision": 1}
    )
    assert stale_adopt.status_code == 409
    assert _source(admin, year)["latest_forecast"]["id"] == second["id"]
    adopted = admin.post(
        f"{PREFIX}/versions/{original['id']}/adopt", params=SCOPE, json={"expected_revision": 2}
    )
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["revision"] == 3
    assert adopted.json()["version"] == original
    assert _source(admin, year)["latest_forecast"]["id"] == original["id"]
    with SessionLocal() as db:
        assert db.query(FinancialPlanVersion).count() == 2
        assert db.query(FinancialPlanningState).one().revision == 3


def test_budget_cannot_be_used_as_frozen_actuals_base(admin, year) -> None:
    budget = _save(admin, year, "budget")["version"]
    cases = [
        ("/preview", {"year": year, "base_version_id": budget["id"]}),
        ("/compare", {"year": year, "baseline_version_id": budget["id"], "base_version_id": budget["id"]}),
        ("/versions", _version_payload(admin, year, "forecast", base_version_id=budget["id"])),
    ]
    for path, payload in cases:
        response = admin.post(f"{PREFIX}{path}", params=SCOPE, json=payload)
        assert response.status_code == 422, response.text
    assert _source(admin, year)["revision"] == 1


@pytest.mark.parametrize("kind", ["forecast", "scenario"])
def test_forecast_and_scenario_bases_preserve_closed_months_only(admin, kind) -> None:
    year = today_in_timezone().year - 1
    contract_id = _seed_contract(year)
    baseline = _save(admin, year, kind, closed_through_month=2)["version"]
    _change_contract_rent(contract_id)
    response = admin.post(
        f"{PREFIX}/preview", params=SCOPE,
        json={"year": year, "closed_through_month": 3, "base_version_id": baseline["id"]},
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["months"][:2] == baseline["snapshot"]["months"][:2]
    assert preview["months"][2]["revenue"] > baseline["snapshot"]["months"][2]["revenue"]
    saved = _save(
        admin, year, "budget", closed_through_month=3, base_version_id=baseline["id"]
    )["version"]
    assert saved["base_version_id"] == baseline["id"]


def test_preview_adjustments_do_not_mutate_contract_or_save_a_version(admin, year) -> None:
    contract_id = _seed_contract(year)
    source = _source(admin, year)["forecast"]
    response = admin.post(
        f"{PREFIX}/preview", params=SCOPE,
        json={"year": year, "contract_adjustments": [
            {"contract_id": contract_id, "start_shift_months": 2, "rent_change_percent": 10}
        ]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["totals"] != source["totals"]
    after = _source(admin, year)
    assert after["forecast"]["totals"] == source["totals"]
    assert after["revision"] == 0
    with SessionLocal() as db:
        assert db.get(Contract, contract_id).start_date == date(year, 1, 1)
        assert db.get(Contract, contract_id).unit_price == 4
        assert db.query(FinancialPlanVersion).count() == 0


def test_foreign_project_ids_are_rejected_without_leaking_or_mutating_rows(admin, year) -> None:
    with SessionLocal() as db:
        other = Building(code="FP_OTHER", name="无权项目哨兵", ingestion_mode="detail", is_active=True)
        db.add(other)
        db.flush()
        expense = FinancialExpense(
            building_id=other.id, name="无权费用哨兵", category="opex", amount_yuan=100,
            occurrence_month=f"{year}-01", payment_month=f"{year}-01", created_by="seed", updated_by="seed",
        )
        version = FinancialPlanVersion(
            building_id=other.id, year=year, revision=1, kind="forecast", name="无权方案哨兵",
            client_state_json="{}", snapshot_json="{}", source_digest="a" * 64, created_by="seed",
        )
        db.add_all([expense, version])
        db.commit()
        expense_id, version_id = int(expense.id), int(version.id)
    contract_id = _seed_contract(year, code="FP_OTHER", suffix="OTHER")
    local = _save(admin, year, "budget")["version"]
    attempts = [
        ("GET", f"/versions/{version_id}", None),
        ("PUT", f"/expenses/{expense_id}", _expense_payload(year, expected_revision=1)),
        ("POST", f"/versions/{version_id}/adopt", {"expected_revision": 1}),
        ("POST", "/preview", {"year": year, "base_version_id": version_id}),
        ("POST", "/versions", _version_payload(admin, year, base_version_id=version_id)),
        ("POST", "/compare", {"year": year, "baseline_version_id": version_id}),
        ("POST", "/compare", {"year": year, "baseline_version_id": local["id"], "base_version_id": version_id}),
        ("POST", "/compare", {"year": year, "baseline_version_id": local["id"], "current_version_id": version_id}),
        ("POST", "/preview", {"year": year, "contract_adjustments": [{"contract_id": contract_id}]}),
    ]
    for method, path, payload in attempts:
        for options in ({}, {"include_baseline": False}) if path == "/compare" else ({},):
            response = admin.request(method, f"{PREFIX}{path}", params={**SCOPE, **options}, json=payload)
            assert response.status_code == 404, (path, response.text)
            assert "哨兵" not in response.text
    for mode in ("live", "workspace", "workspace_deferred", "none"):
        for include in (True, False):
            denied_scope = admin.get(f"{PREFIX}/source", params={"building": "FP_OTHER", "year": year,
                                                             "forecast_mode": mode, "include_latest_snapshot": include})
            assert denied_scope.status_code in {403, 404}
            assert "哨兵" not in denied_scope.text
    with SessionLocal() as db:
        assert db.get(FinancialExpense, expense_id).amount_yuan == 100
        assert db.get(FinancialPlanVersion, version_id).name == "无权方案哨兵"
        assert db.query(FinancialPlanVersion).count() == 2


def test_versions_from_different_year_cannot_be_compared_or_used_as_base(admin, year) -> None:
    baseline = _save(admin, year, "forecast")["version"]
    for path, payload in (
        ("/preview", {"year": year + 1, "base_version_id": baseline["id"]}),
        ("/compare", {"year": year + 1, "baseline_version_id": baseline["id"]}),
        ("/versions", _version_payload(admin, year + 1, base_version_id=baseline["id"])),
    ):
        for options in ({}, {"include_baseline": False}) if path == "/compare" else ({},):
            response = admin.post(f"{PREFIX}{path}", params={**SCOPE, **options}, json=payload)
            assert response.status_code == 404, response.text


@pytest.mark.parametrize("kind", ["forecast", "scenario"])
def test_compare_saved_current_version_uses_immutable_snapshot(admin, year, kind) -> None:
    contract_id = _seed_contract(year)
    expense = _expense(admin, year)
    baseline = _save(admin, year, "budget")["version"]
    _change_contract_rent(contract_id, 5)
    saved = _save(admin, year, kind)["version"]
    compare_payload = {
        "year": year, "baseline_version_id": baseline["id"], "current_version_id": saved["id"]
    }
    before = admin.post(f"{PREFIX}/compare", params=SCOPE, json=compare_payload)
    assert before.status_code == 200, before.text
    assert before.json()["current"] == saved["snapshot"]
    _change_contract_rent(contract_id, 6)
    changed = admin.put(
        f"{PREFIX}/expenses/{expense['id']}", params=SCOPE,
        json=_expense_payload(year, amount_yuan=30000, expected_revision=1),
    )
    assert changed.status_code == 200
    after = admin.post(f"{PREFIX}/compare", params=SCOPE, json=compare_payload)
    assert after.status_code == 200, after.text
    assert after.json() == before.json()
    live = admin.post(
        f"{PREFIX}/compare", params=SCOPE,
        json={"year": year, "baseline_version_id": baseline["id"]},
    )
    assert live.status_code == 200
    assert live.json()["comparison"]["delta"] != after.json()["comparison"]["delta"]


@pytest.mark.parametrize("include_baseline", [True, False])
def test_compare_rejects_budget_and_foreign_year_as_saved_current_version(admin, year, include_baseline) -> None:
    baseline = _save(admin, year, "budget")["version"]
    future = _save(admin, year + 1, "forecast")["version"]
    for version_id, status in ((baseline["id"], 422), (future["id"], 404)):
        response = admin.post(
            f"{PREFIX}/compare", params={**SCOPE, "include_baseline": include_baseline},
            json={"year": year, "baseline_version_id": baseline["id"], "current_version_id": version_id},
        )
        assert response.status_code == status, response.text


def test_simultaneous_version_saves_accept_only_one_matching_revision(admin, year) -> None:
    payload = _version_payload(admin, year, "forecast")
    with TestClient(app) as other:
        _login(other, "admin", "Admin123")
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = [
                pool.submit(client.post, f"{PREFIX}/versions", params=SCOPE, json={**payload, "name": name})
                for client, name in ((admin, "并发预测甲"), (other, "并发预测乙"))
            ]
            responses = [attempt.result(timeout=35) for attempt in attempts]
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json()["version"] for response in responses if response.status_code == 200)
    state = _source(admin, year)
    assert state["revision"] == 1
    assert state["latest_forecast"]["id"] == winner["id"]
    with SessionLocal() as db:
        assert db.query(FinancialPlanVersion).count() == 1


def test_client_cannot_supply_snapshot_or_duplicate_contract_adjustments(admin, year) -> None:
    contract_id = _seed_contract(year)
    for extra in (
        {"snapshot": {"totals": {"revenue": 999999}}},
        {"source_digest": "forged"},
        {"contract_adjustments": [{"contract_id": contract_id}, {"contract_id": contract_id}]},
    ):
        response = admin.post(
            f"{PREFIX}/versions", params=SCOPE,
            json=_version_payload(admin, year, "forecast", **extra),
        )
        assert response.status_code == 422, response.text
    assert _source(admin, year)["revision"] == 0


def test_budget_rounds_each_input_to_cents_before_profit_and_comparison(admin, year) -> None:
    values = [(0.1000005, 0.0000005), (0.0000015, 0.0000005), (0.5555555, 0.1111115)]
    months = [
        {"month": month, "revenue": values[(month - 1) % 3][0], "operating_cost": values[(month - 1) % 3][1]}
        for month in range(1, 13)
    ]
    saved = _save(admin, year, "budget", budget_months=months)["version"]
    snapshot = saved["snapshot"]
    quantum = Decimal("0.000001")  # One cent expressed in the API's ten-thousand-yuan unit.
    for source, row in zip(months, snapshot["months"]):
        revenue = Decimal(str(source["revenue"])).quantize(quantum, rounding=ROUND_HALF_UP)
        cost = Decimal(str(source["operating_cost"])).quantize(quantum, rounding=ROUND_HALF_UP)
        assert Decimal(str(row["revenue"])) == revenue
        assert Decimal(str(row["operating_cost"])) == cost
        assert Decimal(str(row["ebitda"])) == revenue - cost
    for metric in ("revenue", "operating_cost", "ebitda"):
        total = sum(Decimal(str(row[metric])) for row in snapshot["months"])
        assert Decimal(str(snapshot["totals"][metric])) == total
    comparison = admin.post(
        f"{PREFIX}/compare", params=SCOPE,
        json={"year": year, "baseline_version_id": saved["id"]},
    )
    assert comparison.status_code == 200, comparison.text
    result = comparison.json()["comparison"]
    assert Decimal(str(result["delta"])) == -Decimal(str(snapshot["totals"]["ebitda"]))
    assert sum(Decimal(str(row["delta"])) for row in result["items"]) == Decimal(str(result["delta"]))
    assert all(row["source_type"] == "budget_adjustment" for row in result["items"])


def test_expense_save_version_save_and_adopt_write_project_audit_entries(admin, year) -> None:
    expense = _expense(admin, year)
    version = _save(admin, year, "forecast")["version"]
    adopted = admin.post(
        f"{PREFIX}/versions/{version['id']}/adopt", params=SCOPE,
        json={"expected_revision": 1},
    )
    assert adopted.status_code == 200
    expected = {
        "financial_expense_save": ("financial_expense", expense["id"]),
        "financial_plan_save": ("financial_plan", version["id"]),
        "financial_plan_adopt": ("financial_plan", version["id"]),
    }
    for action, (target_type, target_id) in expected.items():
        audit = admin.get("/api/audit/logs", params={**SCOPE, "action": action})
        assert audit.status_code == 200, audit.text
        assert audit.json()["total"] == 1
        entry = audit.json()["items"][0]
        assert entry["operator"] == "admin"
        assert entry["target_type"] == target_type
        assert entry["target_id"] == target_id
        with SessionLocal() as db:
            log = db.query(OperationLog).filter(OperationLog.action == action).one()
            assert log.scope_type == "building"
            assert log.building_id == version["building_id"]
            assert log.portfolio_id is None
            assert log.contract_id is None


@pytest.mark.parametrize("kind", ["forecast", "scenario"])
def test_budget_association_preserves_original_budget_and_target_after_new_approval(admin, year, kind) -> None:
    old_budget = _save(
        admin, year, "budget", name="原批准预算",
        budget_months=[{"month": month, "revenue": 10, "operating_cost": 2} for month in range(1, 13)],
        client_state={"scenario": {"annualTarget": 1200}},
    )["version"]
    historical = _save(
        admin, year, kind, name="原预算下的方案",
        client_state={"scenario": {"annualTarget": 1200}, "strategy": {"mode": "hold", "exitYear": 5}},
    )["version"]
    assert historical["budget_version_id"] == old_budget["id"]
    assert historical["snapshot"]["budget_version_id"] == old_budget["id"]
    before = admin.post(
        f"{PREFIX}/compare", params=SCOPE,
        json={"year": year, "baseline_version_id": old_budget["id"], "current_version_id": historical["id"]},
    )
    assert before.status_code == 200
    new_budget = _save(
        admin, year, "budget", name="新批准预算",
        budget_months=[{"month": month, "revenue": 15, "operating_cost": 4} for month in range(1, 13)],
        client_state={"scenario": {"annualTarget": 2000}},
    )["version"]
    explicit_old = _save(admin, year, kind, budget_version_id=old_budget["id"])["version"]
    implicit_new = _save(admin, year, kind)["version"]
    assert explicit_old["budget_version_id"] == old_budget["id"]
    assert implicit_new["budget_version_id"] == new_budget["id"]
    detail = admin.get(f"{PREFIX}/versions/{historical['id']}", params=SCOPE)
    assert detail.status_code == 200
    assert detail.json() == historical
    restored_budget = admin.get(f"{PREFIX}/versions/{detail.json()['budget_version_id']}", params=SCOPE)
    assert restored_budget.status_code == 200
    assert restored_budget.json()["client_state"]["scenario"]["annualTarget"] == 1200
    assert restored_budget.json()["snapshot"] == old_budget["snapshot"]
    current = _source(admin, year)
    assert current["approved_budget"]["id"] == new_budget["id"]
    summaries = {row["id"]: row for row in current["versions"]}
    assert summaries[historical["id"]]["budget_version_id"] == old_budget["id"]
    assert summaries[explicit_old["id"]]["budget_version_id"] == old_budget["id"]
    assert summaries[implicit_new["id"]]["budget_version_id"] == new_budget["id"]
    after = admin.post(
        f"{PREFIX}/compare", params=SCOPE,
        json={"year": year, "baseline_version_id": old_budget["id"], "current_version_id": historical["id"]},
    )
    assert after.status_code == 200
    assert after.json() == before.json()


@pytest.mark.parametrize("kind", ["forecast", "scenario"])
def test_budget_association_stays_null_for_history_saved_before_first_budget(admin, year, kind) -> None:
    historical = _save(admin, year, kind)["version"]
    assert historical["budget_version_id"] is None
    assert historical["snapshot"]["budget_version_id"] is None
    budget = _save(admin, year, "budget")["version"]
    assert budget["budget_version_id"] is None
    assert budget["snapshot"]["budget_version_id"] is None
    later = _save(admin, year, kind)["version"]
    assert later["budget_version_id"] == budget["id"]
    original = admin.get(f"{PREFIX}/versions/{historical['id']}", params=SCOPE)
    assert original.status_code == 200
    assert original.json() == historical


@pytest.mark.parametrize("kind", ["forecast", "scenario"])
def test_budget_association_rejects_foreign_building_year_and_non_budget_versions(admin, year, kind) -> None:
    wrong_year = _save(admin, year + 1, "budget")["version"]
    wrong_kind = _save(admin, year, "forecast")["version"]
    with SessionLocal() as db:
        building = Building(code="FP_BUDGET_OTHER", name="其他项目预算哨兵", ingestion_mode="detail", is_active=True)
        db.add(building)
        db.flush()
        foreign = FinancialPlanVersion(
            building_id=building.id, year=year, revision=1, kind="budget", name="无权预算哨兵",
            client_state_json="{}", snapshot_json="{}", source_digest="b" * 64, created_by="seed",
        )
        db.add(foreign)
        db.commit()
        foreign_id = foreign.id
    revision = _source(admin, year)["revision"]
    for budget_id, expected_status in ((foreign_id, 404), (wrong_year["id"], 404), (wrong_kind["id"], 422)):
        response = admin.post(
            f"{PREFIX}/versions", params=SCOPE,
            json=_version_payload(admin, year, kind, budget_version_id=budget_id),
        )
        assert response.status_code == expected_status, response.text
        assert "哨兵" not in response.text
    assert _source(admin, year)["revision"] == revision


def test_budget_cannot_associate_another_budget_or_nonpositive_id(admin, year) -> None:
    original = _save(admin, year, "budget")["version"]
    for kind, budget_id in (("budget", original["id"]), ("forecast", 0), ("scenario", -1)):
        response = admin.post(
            f"{PREFIX}/versions", params=SCOPE,
            json=_version_payload(admin, year, kind, budget_version_id=budget_id),
        )
        assert response.status_code == 422, response.text
    assert _source(admin, year)["revision"] == 1


@pytest.mark.parametrize("operation", ["save", "adopt", "expense"])
def test_commit_receipt_keeps_own_revision_when_another_writer_commits_before_response(admin, year, monkeypatch, operation) -> None:
    with TestClient(app) as other:
        _login(other, "admin", "Admin123")
        if operation == "save":
            first_path = f"{PREFIX}/versions"
            second_path = first_path
            method = "POST"
            first_payload = _version_payload(admin, year, "forecast", name="先提交的预测")
            second_payload = {**first_payload, "name": "随后提交的预测", "expected_revision": 1}
            first_revision, final_revision = 1, 2
        elif operation == "adopt":
            original = _save(admin, year, "forecast", name="原预测")["version"]
            latest = _save(admin, year, "forecast", name="新预测")["version"]
            first_path = f"{PREFIX}/versions/{original['id']}/adopt"
            second_path = f"{PREFIX}/versions/{latest['id']}/adopt"
            method = "POST"
            first_payload, second_payload = {"expected_revision": 2}, {"expected_revision": 3}
            first_revision, final_revision = 3, 4
        else:
            expense = _expense(admin, year)
            first_path = f"{PREFIX}/expenses/{expense['id']}"
            second_path = first_path
            method = "PUT"
            first_payload = _expense_payload(year, amount_yuan=30000, expected_revision=1)
            second_payload = _expense_payload(year, amount_yuan=40000, expected_revision=2)
            first_revision, final_revision = 2, 3

        original_commit = Session.commit
        second_responses = []
        first_committed = False

        def commit_then_finish_other_request(session):
            nonlocal first_committed
            original_commit(session)
            if not first_committed:
                first_committed = True
                # Keep real authentication, authorization, transactions and writes.
                # Only schedule the second writer in the precise commit/response gap.
                second_responses.append(other.request(method, second_path, params=SCOPE, json=second_payload))

        with monkeypatch.context() as patch:
            patch.setattr(Session, "commit", commit_then_finish_other_request)
            first = admin.request(method, first_path, params=SCOPE, json=first_payload)

        assert first.status_code == 200, first.text
        assert len(second_responses) == 1
        assert second_responses[0].status_code == 200, second_responses[0].text
        assert first.json()["revision"] == first_revision
        assert second_responses[0].json()["revision"] == final_revision
        if operation == "expense":
            assert first.json()["amount_yuan"] == 30000
            assert second_responses[0].json()["amount_yuan"] == 40000
            with SessionLocal() as db:
                assert db.get(FinancialExpense, expense["id"]).revision == final_revision
        else:
            source = _source(admin, year)
            assert source["revision"] == final_revision
            assert source["latest_forecast"]["id"] == second_responses[0].json()["version"]["id"]
            stale = admin.post(
                f"{PREFIX}/versions", params=SCOPE,
                json=_version_payload(admin, year, "forecast", expected_revision=first.json()["revision"]),
            )
            assert stale.status_code == 409


def test_viewer_can_read_and_preview_but_cannot_write(admin, year) -> None:
    expense = _expense(admin, year)
    baseline = _save(admin, year, "budget")["version"]
    created = admin.post(
        "/api/auth/create-user",
        json={"username": "viewer", "password": "Viewer123", "display_name": "Viewer", "role": "viewer"},
    )
    assert created.status_code == 200
    with TestClient(app) as viewer:
        _login(viewer, "viewer", "Viewer123")
        assert _source(viewer, year)["can_write"] is False
        for mode in ("live", "workspace", "none"):
            response = viewer.get(f"{PREFIX}/source", params={**SCOPE, "year": year, "forecast_mode": mode})
            assert response.status_code == 200, response.text
            assert response.json()["can_write"] is False
            assert response.json()["approved_budget"] == baseline
            assert response.json()["expenses"][0]["id"] == expense["id"]
        for path in ("/expenses", "/versions", f"/versions/{baseline['id']}"):
            response = viewer.get(f"{PREFIX}{path}", params={**SCOPE, "year": year})
            assert response.status_code == 200, response.text
        for path, payload in (
            ("/preview", {"year": year}),
            ("/compare", {"year": year, "baseline_version_id": baseline["id"]}),
        ):
            assert viewer.post(f"{PREFIX}{path}", params=SCOPE, json=payload).status_code == 200
        assert viewer.post(f"{PREFIX}/compare", params={**SCOPE, "include_baseline": False},
                           json={"year": year, "baseline_version_id": baseline["id"]}).status_code == 200
        for method, path, payload in (
            ("POST", "/expenses", _expense_payload(year)),
            ("PUT", f"/expenses/{expense['id']}", _expense_payload(year, expected_revision=1)),
            ("POST", "/versions", _version_payload(admin, year)),
            ("POST", f"/versions/{baseline['id']}/adopt", {"expected_revision": 1}),
        ):
            response = viewer.request(method, f"{PREFIX}{path}", params=SCOPE, json=payload)
            # The shared scope guard masks inaccessible write scopes as not found.
            assert response.status_code == 404, response.text


def test_portfolio_overview_only_account_cannot_read_or_write_planning(admin, year) -> None:
    baseline = _save(admin, year, "budget")["version"]
    created = admin.post(
        "/api/auth/create-user",
        json={"username": "overview", "password": "Overview123", "display_name": "概况账号",
              "role": "viewer", "initial_scope": "none"},
    )
    assert created.status_code == 200
    users = admin.get("/api/auth/users").json()
    user_id = next(row["id"] for row in users if row["username"] == "overview")
    authorization = admin.get(f"/api/auth/authorizations/users/{user_id}").json()
    changed = admin.put(
        f"/api/auth/authorizations/users/{user_id}",
        json={"expected_authorization_version": authorization["authorization_version"],
              "grants": [{"scope_type": "portfolio_overview", "scope_code": "DEMO_OFFICE_PORTFOLIO",
                          "access_level": "view", "include_future_buildings": False}], "capabilities": []},
    )
    assert changed.status_code == 200, changed.text
    with TestClient(app) as overview:
        _login(overview, "overview", "Overview123")
        for selected in (SCOPE, {"portfolio": "DEMO_OFFICE_PORTFOLIO"}):
            for mode in ("live", "workspace", "none"):
                response = overview.get(f"{PREFIX}/source", params={**selected, "year": year, "forecast_mode": mode})
                assert response.status_code == 404, response.text
            slim = overview.post(f"{PREFIX}/compare", params={**selected, "include_baseline": False},
                                 json={"year": year, "baseline_version_id": baseline["id"]})
            assert slim.status_code == 404, slim.text
            for path in ("/source", "/expenses", "/versions", f"/versions/{baseline['id']}"):
                response = overview.get(f"{PREFIX}{path}", params={**selected, "year": year})
                assert response.status_code == 404, response.text
            for path, payload in (
                ("/preview", {"year": year}),
                ("/compare", {"year": year, "baseline_version_id": baseline["id"]}),
                ("/expenses", _expense_payload(year)),
                ("/versions", _version_payload(admin, year)),
                (f"/versions/{baseline['id']}/adopt", {"expected_revision": 1}),
            ):
                response = overview.post(f"{PREFIX}{path}", params=selected, json=payload)
                assert response.status_code == 404, response.text
