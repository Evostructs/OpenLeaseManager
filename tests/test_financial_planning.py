from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
import json

from fastapi import HTTPException
import pytest

from app.database import SessionLocal
from app.models import Building, Contract, ReceivablePayment, ReceivableRecord, ReceivableStatement, RentTerm
from app.services.financial_planning import build_financial_forecast, compare_financial_snapshots


@pytest.fixture
def planning_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def _building_id(db, code="DEMO_A"):
    return db.query(Building.id).filter(Building.code == code).scalar()


def _contract(db, number="FIN-1", **values):
    defaults = {
        "building_id": _building_id(db), "contract_no": number,
        "room_no": f"ROOM-{number}", "tower": "南塔", "tenant_name": "测试承租人",
        "area": 100, "monthly_rent": 1000, "unit_price": 0,
        "start_date": date(2026, 1, 1), "end_date": date(2026, 12, 31),
        "free_period": 0, "property_fee_rate": 0, "is_deleted": False,
    }
    defaults.update(values)
    contract = Contract(**defaults)
    db.add(contract)
    db.flush()
    return contract


def _payment(db, contract, period, when, amount, allocation="收款"):
    payment = ReceivablePayment(
        contract_id=contract.id, period_month=period, payment_date=when,
        amount=amount, allocation_type=allocation, source="manual",
    )
    db.add(payment)
    db.flush()
    return payment


def _forecast(db, **options):
    return build_financial_forecast(db, building_id=_building_id(db), year=options.pop("year", 2026), **options)


def _row(snapshot, month):
    return next(row for row in snapshot["months"] if row["month"] == month)


def _fen(wan):
    return int(Decimal(str(wan)) * 1_000_000)


def _assert_additive(snapshot):
    for metric, total in snapshot["totals"].items():
        assert sum(_fen(row[metric]) for row in snapshot["months"]) == _fen(total), metric
        for month in range(1, 13):
            assert sum(_fen(_row(source, month)[metric]) for source in snapshot["sources"]) == _fen(_row(snapshot, month)[metric]), metric


def test_rent_free_is_spread_over_full_lease_but_receivables_and_property_are_separate(planning_db):
    contract = _contract(planning_db, free_period=2, property_fee_rate=2)
    report = _forecast(planning_db)
    january = _row(report, 1)
    assert january["rent_income"] == 0.083333
    assert january["rent_receivable"] == 0
    assert january["property_fee_income"] == 0.02
    assert _row(report, 3)["rent_receivable"] == 0.1
    assert report["totals"]["rent_income"] == 1
    assert report["totals"]["rent_receivable"] == 1
    assert report["totals"]["revenue"] == 1.24
    assert report["totals"]["forecast_cash_received"] == 1
    assert report["coverage"]["property_fee_cash_included"] is False
    assert report["sources"][0]["contract_id"] == contract.id
    assert [row["month"] for row in report["months"]] == list(range(1, 13))
    assert january["period"] == "2026-01"
    _assert_additive(report)


def test_leap_year_partial_month_and_all_free_contract_preserve_cent_totals(planning_db):
    _contract(planning_db, start_date=date(2024, 1, 15), end_date=date(2024, 3, 14), free_period=1)
    report = _forecast(planning_db, year=2024)
    # Jan 15-Feb 14 is free; Feb 15-29 = 15/29 and Mar 1-14 = 14/31.
    assert report["totals"]["rent_receivable"] == 0.096885
    assert report["totals"]["rent_income"] == 0.096885
    assert _row(report, 1)["rent_income"] > 0
    assert _row(report, 1)["rent_receivable"] == 0
    assert _row(report, 4)["rent_income"] == 0
    _assert_additive(report)
    _contract(planning_db, "FREE", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31), free_period=12)
    assert _forecast(planning_db, year=2024)["totals"]["rent_income"] == 0.096885


def test_known_rent_terms_share_billing_engine_and_straight_line_total(planning_db):
    contract = _contract(planning_db)
    planning_db.add_all([
        RentTerm(contract_id=contract.id, rule_type="fixed_monthly", start_date=date(2026, 1, 1), end_date=date(2026, 6, 30), monthly_amount=1000),
        RentTerm(contract_id=contract.id, rule_type="stepped_rent", start_date=date(2026, 7, 1), end_date=date(2026, 12, 31), monthly_amount=2000),
        RentTerm(contract_id=contract.id, rule_type="rent_free", start_date=date(2026, 2, 1), end_date=date(2026, 2, 28)),
    ])
    planning_db.flush()
    report = _forecast(planning_db)
    assert _row(report, 2)["rent_receivable"] == 0
    assert _row(report, 7)["rent_receivable"] == 0.2
    assert report["totals"]["rent_receivable"] == 1.7
    assert report["totals"]["rent_income"] == 1.7
    assert _row(report, 1)["rent_income"] == 0.141667
    _assert_additive(report)


def test_frozen_billing_statement_is_used_without_making_cash_or_revenue_equal_to_bill(planning_db):
    contract = _contract(planning_db, property_fee_rate=2)
    planning_db.add(ReceivableStatement(contract_id=contract.id, period_month="2026-02", rent=900, property_fee=400, total_amount=1300))
    planning_db.flush()
    report = _forecast(planning_db, closed_through_month=2)
    february = _row(report, 2)
    assert february["rent_receivable"] == 0.09
    assert february["rent_income"] == 0.1
    assert february["property_fee_receivable"] == 0.04
    assert february["actual_cash_received"] == 0
    assert report["coverage"]["frozen_receivable_months"] == 1


def test_cash_uses_payment_date_across_years_and_only_settlements_before_cutoff(planning_db):
    contract = _contract(planning_db, monthly_rent=10000, start_date=date(2025, 1, 1), end_date=date(2027, 12, 31))
    _payment(planning_db, contract, "2025-12", date(2026, 1, 5), 2000)
    _payment(planning_db, contract, "2025-12", date(2026, 1, 8), 3000, "冲抵")
    _payment(planning_db, contract, "2026-09", date(2026, 8, 20), 6000)
    _payment(planning_db, contract, "2026-09", date(2026, 8, 21), 1000, "减免")
    later = _payment(planning_db, contract, "2026-10", date(2026, 9, 1), 4000)
    _payment(planning_db, contract, "2026-12", date(2027, 1, 3), 10000)
    _payment(planning_db, contract, "2027-01", date(2026, 8, 6), 3000)
    report = _forecast(planning_db, closed_through_month=8)
    assert _row(report, 1)["actual_cash_received"] == 0.2
    assert _row(report, 8)["actual_cash_received"] == 0.9
    assert _row(report, 9)["forecast_cash_received"] == 0.3
    assert _row(report, 10)["forecast_cash_received"] == 1
    assert _row(report, 12)["forecast_cash_received"] == 1
    assert _row(report, 9)["actual_cash_received"] == 0
    assert report["totals"]["actual_cash_received"] == 1.1
    assert report["coverage"]["past_due_rent_balance_yuan"] > 0
    assert any(row["id"] == later.id for row in report["sources"][0]["source_snapshot"]["payments"])
    _assert_additive(report)


def test_legacy_payments_are_not_resurrected_or_silently_assigned_a_date(planning_db):
    contract = _contract(planning_db)
    for month, amount, received in [(4, 888, date(2026, 4, 2)), (5, 777, date(2026, 5, 5)), (6, 999, date(2026, 6, 1)), (7, 222, None)]:
        planning_db.add(ReceivableRecord(contract_id=contract.id, period_month=f"2026-{month:02d}", received_amount=amount, received_date=received))
    planning_db.flush()
    _payment(planning_db, contract, "2026-04", date(2026, 4, 3), 200, "冲抵")
    _payment(planning_db, contract, "2026-06", date(2026, 6, 3), 250)
    report = _forecast(planning_db, closed_through_month=8)
    assert _row(report, 4)["actual_cash_received"] == 0
    assert _row(report, 5)["actual_cash_received"] == 0.0777
    assert _row(report, 6)["actual_cash_received"] == 0.025
    assert _row(report, 7)["actual_cash_received"] == 0
    assert report["coverage"]["undated_cash_yuan"] == 222
    assert report["coverage"]["unresolved_payments"][0]["period_month"] == "2026-07"


def test_expense_occurrence_actual_payment_capex_and_unpaid_carry_are_distinct(planning_db):
    expenses = [
        {"id": 1, "name": "物业服务", "category": "opex", "amount_yuan": Decimal("1000.01"), "occurrence_month": "2026-03", "payment_month": "2026-04", "paid_on": date(2026, 4, 10)},
        {"id": 2, "name": "设备改造", "category": "capex", "amount_yuan": 5000, "occurrence_month": "2026-05", "payment_month": "2026-06", "paid_on": date(2026, 6, 2)},
        {"id": 3, "name": "尚未支付", "category": "opex", "amount_yuan": 700, "occurrence_month": "2026-02", "payment_month": "2026-03", "paid_on": None},
        {"id": 4, "name": "跨年费用", "category": "opex", "amount_yuan": 300, "occurrence_month": "2026-12", "payment_month": "2027-01", "paid_on": None},
        {"id": 5, "name": "往年已付", "category": "opex", "amount_yuan": 200, "occurrence_month": "2026-01", "payment_month": "2025-12", "paid_on": date(2025, 12, 20)},
    ]
    report = _forecast(planning_db, closed_through_month=8, expenses=expenses)
    assert _row(report, 3)["ebitda"] == -0.100001
    assert _row(report, 3)["net_cash_flow"] == 0
    assert _row(report, 4)["net_cash_flow"] == -0.100001
    assert _row(report, 5)["capex"] == 0.5
    assert _row(report, 5)["ebitda"] == 0
    assert _row(report, 6)["net_cash_flow"] == -0.5
    assert _row(report, 9)["forecast_opex_paid"] == 0.07
    assert _row(report, 12)["ebitda"] == -0.03
    assert _row(report, 12)["net_cash_flow"] == 0
    assert _row(report, 1)["net_cash_flow"] == 0
    assert report["coverage"]["expense_payable_yuan"] == 700
    assert report["coverage"]["carried_expenses"][0]["forecast_payment_month"] == "2026-09"
    json.dumps(report, allow_nan=False)
    _assert_additive(report)
    closed = _forecast(planning_db, closed_through_month=12, expenses=expenses)
    assert closed["totals"]["forecast_opex_paid"] == 0
    assert closed["coverage"]["expense_payable_yuan"] == 700
    assert closed["coverage"]["carried_expenses"][0]["forecast_payment_month"] is None


def test_expense_payment_after_cutoff_remains_a_plan_and_missing_fields_are_rejected(planning_db):
    expense = {"id": 1, "category": "opex", "name": "费用", "amount_yuan": 1000, "occurrence_month": "2026-03", "payment_month": "2026-10", "paid_on": "2026-09-01"}
    report = _forecast(planning_db, closed_through_month=8, expenses=[expense])
    assert report["totals"]["actual_opex_paid"] == 0
    assert _row(report, 10)["forecast_opex_paid"] == 0.1
    for change in [{"amount_yuan": None}, {"amount_yuan": -1}, {"category": "unknown"}, {"payment_month": ""}, {"occurrence_month": "2026-13"}]:
        with pytest.raises(HTTPException) as error:
            _forecast(planning_db, expenses=[{**expense, **change}])
        assert error.value.status_code == 422


def test_contract_adjustment_recalculates_plan_without_mutation_or_duplicate_rent(planning_db):
    contract = _contract(planning_db, start_date=date(2026, 9, 1), end_date=date(2027, 8, 31), free_period=1)
    _payment(planning_db, contract, "2026-10", date(2026, 8, 10), 500)
    original = _forecast(planning_db, closed_through_month=8)
    adjusted = _forecast(planning_db, closed_through_month=8, contract_adjustments=[{"contract_id": contract.id, "start_shift_months": 1, "rent_change_percent": -10}])
    assert _row(adjusted, 9)["rent_income"] == 0
    assert _row(adjusted, 10)["rent_receivable"] == 0
    assert _row(adjusted, 11)["rent_receivable"] == 0.09
    assert _row(adjusted, 11)["forecast_cash_received"] == 0.04
    assert len(adjusted["sources"]) == 1
    assert original["totals"]["rent_income"] > adjusted["totals"]["rent_income"]
    planning_db.expire_all()
    persisted = planning_db.get(Contract, contract.id)
    assert persisted.start_date == date(2026, 9, 1)
    assert persisted.monthly_rent == 1000
    assert persisted.rent_terms == []
    _assert_additive(adjusted)


def test_adjustment_moves_explicit_rent_terms_and_rejects_duplicate_contract_inputs(planning_db):
    contract = _contract(planning_db, start_date=date(2026, 9, 15), end_date=date(2027, 9, 14))
    planning_db.add(RentTerm(contract_id=contract.id, rule_type="fixed_monthly", start_date=contract.start_date, end_date=contract.end_date, monthly_amount=1500))
    planning_db.flush()
    adjustment = {"contract_id": contract.id, "start_shift_months": 1, "rent_change_percent": 0}
    report = _forecast(planning_db, contract_adjustments=[adjustment])
    assert _row(report, 9)["rent_receivable"] == 0
    assert _row(report, 10)["rent_receivable"] == 0.082258
    assert _row(report, 11)["rent_receivable"] == 0.15
    with pytest.raises(HTTPException):
        _forecast(planning_db, contract_adjustments=[adjustment, adjustment])


def test_saved_closed_months_and_their_sources_survive_edits_and_contract_exit(planning_db):
    contract = _contract(planning_db)
    previous = _forecast(planning_db, closed_through_month=3)
    saved = deepcopy(previous)
    contract.monthly_rent = 2000
    planning_db.flush()
    current = _forecast(planning_db, closed_through_month=5, frozen_snapshot=previous)
    assert previous == saved
    assert [_row(current, month)["rent_income"] for month in range(1, 7)] == [0.1, 0.1, 0.1, 0.2, 0.2, 0.2]
    assert current["totals"]["rent_income"] == 2.1
    assert current["coverage"]["frozen_through_month"] == 3
    contract.is_deleted = True
    planning_db.flush()
    exited = _forecast(planning_db, closed_through_month=6, frozen_snapshot=current)
    assert exited["totals"]["rent_income"] == 0.7
    assert _row(exited, 6)["rent_income"] == 0
    periods = exited["sources"][0]["source_snapshot"]["frozen_periods"]
    assert [(item["start_month"], item["end_month"]) for item in periods] == [(1, 3), (4, 5)]
    assert periods[0]["source_snapshot"]["contract"]["monthly_rent"] == 1000
    assert periods[1]["source_snapshot"]["contract"]["monthly_rent"] == 2000
    assert "frozen_periods" not in periods[0]["source_snapshot"]
    _assert_additive(exited)


def test_direct_engine_adjustments_use_same_limits_as_api(planning_db):
    contract = _contract(planning_db)
    for adjustment in [{"start_shift_months": -25}, {"start_shift_months": 25}, {"rent_change_percent": -101}, {"rent_change_percent": 501}]:
        with pytest.raises(HTTPException) as error:
            _forecast(planning_db, contract_adjustments=[{"contract_id": contract.id, **adjustment}])
        assert error.value.status_code == 422
    zero_rent = _forecast(planning_db, contract_adjustments=[{"contract_id": contract.id, "rent_change_percent": -100}])
    assert zero_rent["totals"]["rent_income"] == 0
    assert zero_rent["totals"]["forecast_cash_received"] == 0


def test_cross_project_inputs_and_irrelevant_private_fields_never_enter_snapshot(planning_db):
    contract = _contract(planning_db, contact_phone="private-phone", contact_email="private-email", notes="private-notes")
    other_id = _building_id(planning_db, "DEMO_B")
    other = _contract(planning_db, "OTHER", building_id=other_id, monthly_rent=9000000)
    _payment(planning_db, other, "2026-01", date(2026, 1, 1), 8000000)
    report = _forecast(planning_db, closed_through_month=1)
    assert report["totals"]["rent_income"] == 1.2
    assert report["totals"]["actual_cash_received"] == 0
    assert [source["contract_id"] for source in report["sources"]] == [contract.id]
    encoded = json.dumps(report, ensure_ascii=False, allow_nan=False)
    assert "private-phone" not in encoded
    assert "private-email" not in encoded
    assert "private-notes" not in encoded
    with pytest.raises(HTTPException) as error:
        _forecast(planning_db, contract_adjustments=[{"contract_id": other.id}])
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        _forecast(planning_db, expenses=[{"id": 1, "building_id": other_id}])
    assert error.value.status_code == 404


def test_comparison_reconciles_contract_expense_and_manual_budget_sources(planning_db):
    contract = _contract(planning_db)
    baseline = _forecast(planning_db)
    adjustment_months = [{"month": month, "revenue": 0.35 if month == 1 else 0, "operating_cost": 0.05 if month == 1 else 0, "ebitda": 0.3 if month == 1 else 0} for month in range(1, 13)]
    baseline["sources"].append({"source_key": "budget_adjustment:manual", "source_type": "budget_adjustment", "source_id": "manual", "label": "预算编制调整", "months": adjustment_months, "source_snapshot": {}})
    for metric, value in {"revenue": 0.35, "operating_cost": 0.05, "ebitda": 0.3}.items():
        baseline["months"][0][metric] += value
        baseline["totals"][metric] += value
    contract.monthly_rent = 1200
    planning_db.flush()
    current = _forecast(planning_db, expenses=[{"id": 1, "name": "维修", "category": "opex", "amount_yuan": 2000, "occurrence_month": "2026-03", "payment_month": "2026-04", "paid_on": None}])
    comparison = compare_financial_snapshots(baseline, current)
    assert comparison["baseline_total"] == 1.5
    assert comparison["current_total"] == 1.24
    assert comparison["delta"] == -0.26
    assert sum(_fen(item["delta"]) for item in comparison["items"]) == _fen(comparison["delta"])
    assert {item["reason"] for item in comparison["items"]} == {"合同条款变化", "费用变化", "预算编制调整"}
    for month in range(1, 13):
        assert sum(_fen(_row(item, month)["delta"]) for item in comparison["items"]) == _fen(_row(current, month)["ebitda"]) - _fen(_row(baseline, month)["ebitda"])
    assert all(item["source_type"] != "other" for item in comparison["items"])


def test_unattributed_difference_is_explicit_and_cash_does_not_change_ebitda(planning_db):
    contract = _contract(planning_db)
    before = _forecast(planning_db, closed_through_month=1)
    _payment(planning_db, contract, "2026-01", date(2026, 1, 5), 500)
    after = _forecast(planning_db, closed_through_month=1)
    assert compare_financial_snapshots(before, after)["delta"] == 0
    cash = compare_financial_snapshots(before, after, "net_cash_flow")
    assert cash["delta"] == 0.05
    assert cash["items"][0]["reason"] == "合同收款及计划变化"
    after["months"][0]["ebitda"] += 0.02
    after["totals"]["ebitda"] += 0.02
    variance = compare_financial_snapshots(before, after)
    assert variance["items"][0]["source_key"] == "other:unattributed"
    assert variance["items"][0]["delta"] == 0.02
    after["totals"]["ebitda"] += 1
    with pytest.raises(HTTPException):
        compare_financial_snapshots(before, after)


def test_total_bar_sources_include_unchanged_contract_and_reconcile_each_month(planning_db):
    changed = _contract(planning_db, "CHANGED", monthly_rent=1_000_000, end_date=date(2026, 1, 31))
    unchanged = _contract(planning_db, "UNCHANGED", monthly_rent=2_000_000, end_date=date(2026, 1, 31))
    before = _forecast(planning_db)
    changed.monthly_rent = 1_100_000
    planning_db.flush()
    after = _forecast(planning_db)
    comparison = compare_financial_snapshots(before, after)
    assert comparison["baseline_total"] == 300
    assert comparison["current_total"] == 310
    assert comparison["delta"] == 10
    assert [item["contract_id"] for item in comparison["items"]] == [changed.id]
    assert {item["contract_id"] for item in comparison["all_sources"]} == {changed.id, unchanged.id}
    same = next(item for item in comparison["all_sources"] if item["contract_id"] == unchanged.id)
    assert same["reason"] == "金额未变"
    assert same["baseline_value"] == same["current_value"] == 200
    for key, total in [("baseline_value", 300), ("current_value", 310), ("delta", 10)]:
        assert sum(_fen(item[key]) for item in comparison["all_sources"]) == _fen(total)
    for month in range(1, 13):
        assert sum(_fen(_row(item, month)["baseline_value"]) for item in comparison["all_sources"]) == _fen(_row(before, month)["ebitda"])
        assert sum(_fen(_row(item, month)["current_value"]) for item in comparison["all_sources"]) == _fen(_row(after, month)["ebitda"])


def test_unchanged_unattributed_amount_is_retained_for_total_drilldown(planning_db):
    _contract(planning_db)
    before = _forecast(planning_db)
    before["months"][0]["ebitda"] += 0.02
    before["totals"]["ebitda"] += 0.02
    comparison = compare_financial_snapshots(before, deepcopy(before))
    assert comparison["items"] == []
    residual = next(item for item in comparison["all_sources"] if item["source_key"] == "other:unattributed")
    assert residual["reason"] == "金额未变"
    assert residual["baseline_value"] == residual["current_value"] == 0.02
    assert sum(_fen(item["baseline_value"]) for item in comparison["all_sources"]) == _fen(comparison["baseline_total"])


def test_forecast_is_read_only_and_rejects_budget_freeze_or_foreign_year(planning_db):
    _contract(planning_db)
    report = _forecast(planning_db, closed_through_month=2)
    assert not planning_db.new
    assert not planning_db.dirty
    invalid = deepcopy(report)
    invalid["year"] = 2025
    with pytest.raises(HTTPException):
        _forecast(planning_db, frozen_snapshot=invalid)
    invalid = deepcopy(report)
    invalid["sources"].append({"source_key": "budget_adjustment:1", "source_type": "budget_adjustment", "months": []})
    with pytest.raises(HTTPException):
        _forecast(planning_db, frozen_snapshot=invalid)
