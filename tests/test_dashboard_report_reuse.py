from __future__ import annotations

from collections import Counter
from datetime import date, datetime

import pytest
from sqlalchemy import event

from app import models
from app.database import SessionLocal, engine
from app.models import (
    Attachment,
    Building,
    Contract,
    ContractDeposit,
    ContractRoom,
    Customer,
    CustomerFollowUp,
    ReceivablePayment,
    ReceivableRecord,
    ReceivableStatement,
    RentTerm,
    Room,
    Zone,
)
from app.services import contract_receivables, contracts, dashboard, risks, rooms
from app.services.receivable_history import cached_monthly_report, collect_overdue_history


TODAY = date(2026, 9, 11)
STAMP = datetime(2026, 9, 1, 10, 0)


@pytest.fixture
def dashboard_case(monkeypatch):
    for module in (models, contract_receivables, contracts, dashboard, risks, rooms):
        monkeypatch.setattr(module, "today_in_timezone", lambda: TODAY)

    building_ids = []
    contract_ids = []
    customer_ids = []
    with SessionLocal() as db:
        for building_index in range(2):
            building = Building(code=f"DASH-REUSE-{building_index}", name=f"Dashboard {building_index}")
            db.add(building)
            db.flush()
            building_ids.append(building.id)
            zone = Zone(
                building_id=building.id, code="MAIN", name="Main",
                zone_type="tower", valid_from=date(2025, 1, 1),
            )
            db.add(zone)
            db.flush()
            for local_index in range(2):
                index = building_index * 2 + local_index
                room_no = f"DASH-{index}"
                rent = 3100.25 + index * 1000
                customer = Customer(name=f"Dashboard customer {index}", contact_phone=f"test-phone-{index}")
                room = Room(building_id=building.id, zone_id=zone.id, tower="南塔", room_no=room_no, area=100)
                db.add_all([customer, room])
                db.flush()
                customer_ids.append(customer.id)
                contract = Contract(
                    contract_no=f"DASH-REUSE-{index}", building_id=building.id,
                    customer_id=customer.id, tenant_name=customer.name,
                    tower="南塔", room_no=room_no, area=100, monthly_rent=rent,
                    property_fee_rate=2.5, payment_due_day=5,
                    start_date=date(2025, 10, 1), end_date=date(2026, 10, 31),
                    internal_contact_person=f"owner-{index}",
                    updated_at=STAMP,
                )
                contract.room_items = [ContractRoom(
                    building_id=building.id, room_id=room.id, tower="南塔", room_no=room_no,
                    area=100, monthly_rent=rent, start_date=contract.start_date, end_date=contract.end_date,
                )]
                contract.rent_terms = [RentTerm(
                    rule_type="fixed_monthly", start_date=contract.start_date,
                    end_date=contract.end_date, monthly_amount=rent,
                )]
                contract.deposits = [ContractDeposit(
                    customer_id=customer.id, expected_amount=5000.25, received_amount=1250.10,
                    refunded_amount=50.05, offset_amount=100.05, due_date=date(2026, 8, 5),
                )]
                db.add(contract)
                db.flush()
                contract_ids.append(contract.id)
                db.add_all([
                    # A prior month deliberately differs from current rent terms.
                    ReceivableStatement(
                        contract_id=contract.id, period_month="2026-08", rent=2222.22 + index,
                        property_fee=250, total_amount=2472.22 + index,
                        source="manual", generated_at=STAMP,
                    ),
                    ReceivablePayment(
                        contract_id=contract.id, period_month="2026-08", amount=111.11,
                        payment_date=date(2026, 8, 6), created_at=STAMP,
                    ),
                    ReceivablePayment(
                        contract_id=contract.id, period_month="2026-09", amount=333.33,
                        payment_date=date(2026, 9, 6), created_at=STAMP,
                    ),
                    ReceivableRecord(
                        contract_id=contract.id, period_month="2026-08", invoice_status="已开票",
                        invoice_no=f"DASH-INVOICE-{index}", dunning_owner=f"collector-{index}",
                        next_follow_up_date=date(2026, 9, 10), notes="Keep the complete source row",
                    ),
                    CustomerFollowUp(
                        customer_id=customer.id, contract_id=contract.id, building_id=building.id,
                        next_follow_up_date=date(2026, 9, 10), next_action=f"Follow up {index}",
                        owner=f"owner-{index}", occurred_at=STAMP,
                    ),
                ])
                if local_index == 0:
                    db.add(Attachment(
                        scope_type="building", building_id=building.id,
                        entity_type="contract", entity_id=contract.id, category="合同正文",
                        file_name=f"DASH-{index}.pdf", storage_path=f"DASH-{index}.pdf",
                    ))
        db.commit()
    return {"building_ids": building_ids, "contract_ids": contract_ids, "customer_ids": customer_ids}


def _read_dashboard(monkeypatch, building_ids, *, old_month_keys=False):
    calls = Counter()
    batch_context = contract_receivables._build_receivable_batch_context
    monthly_report = contract_receivables.get_monthly_receivables
    list_risks = risks.list_risks

    def counted_context(*args, **kwargs):
        calls["batches"] += 1
        return batch_context(*args, **kwargs)

    def counted_report(*args, **kwargs):
        calls["months"] += 1
        return monthly_report(*args, **kwargs)

    def with_old_month_keys(*args, **kwargs):
        # Reproduce the old dashboard boundary without changing risk/SQL logic.
        kwargs["monthly_reports"] = {
            key.split(":", 1)[0]: value for key, value in kwargs["monthly_reports"].items()
        }
        return list_risks(*args, **kwargs)

    def capture_sql(_connection, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select "):
            calls["selects"] += 1
            for table in ("rent_terms", "receivable_statements", "receivable_records", "receivable_payments"):
                if f"from {table} " in normalized:
                    calls[table] += 1

    with monkeypatch.context() as patch:
        patch.setattr(contract_receivables, "_build_receivable_batch_context", counted_context)
        patch.setattr(contract_receivables, "get_monthly_receivables", counted_report)
        if old_month_keys:
            patch.setattr(risks, "list_risks", with_old_month_keys)
        event.listen(engine, "before_cursor_execute", capture_sql)
        try:
            # Neither setup nor the preceding read may warm this identity map.
            with SessionLocal() as db:
                result = dashboard.get_operations_dashboard(db, building_ids=building_ids, risk_limit=100)
        finally:
            event.remove(engine, "before_cursor_execute", capture_sql)
    return result, calls


@pytest.mark.parametrize("scope", ["single", "multiple", "empty", "global"])
def test_dashboard_reuses_same_scope_reports_without_changing_complete_result(monkeypatch, dashboard_case, scope):
    all_buildings = dashboard_case["building_ids"]
    building_ids = {"single": all_buildings[:1], "multiple": all_buildings, "empty": [], "global": None}[scope]
    expected, old_calls = _read_dashboard(monkeypatch, building_ids, old_month_keys=True)
    actual, new_calls = _read_dashboard(monkeypatch, building_ids)

    assert actual == expected  # Includes full queue source rows, risk metadata and cents.
    assert new_calls["batches"] == 1
    assert new_calls["months"] == 12
    assert old_calls["batches"] == (1 if scope == "global" else 2)
    assert old_calls["months"] == (12 if scope == "global" else 24)
    expected_ids = set(dashboard_case["contract_ids"][:2] if scope == "single" else dashboard_case["contract_ids"])
    expected_customer_ids = set(dashboard_case["customer_ids"][:2] if scope == "single" else dashboard_case["customer_ids"])
    if scope == "empty":
        expected_ids = set()
        expected_customer_ids = set()
    assert {row["contract_id"] for row in actual["queues"]["overdue_contracts"]} == expected_ids
    for row in actual["risks"]["top"]:
        # Missing customer-license risks identify the customer/building only.
        assert row["customer_id"] in expected_customer_ids
        if "contract_id" in row:
            assert row["contract_id"] in expected_ids
        if "building_id" in row:
            assert row["building_id"] in (all_buildings if building_ids is None else building_ids)

    if scope in {"single", "multiple"}:
        # The second batch reread three ledgers and each expired rent-term collection.
        for table in ("receivable_statements", "receivable_records", "receivable_payments"):
            assert old_calls[table] - new_calls[table] == 1
        assert old_calls["rent_terms"] - new_calls["rent_terms"] == len(expected_ids)
        assert old_calls["selects"] - new_calls["selects"] == 3 + len(expected_ids)
    else:
        assert old_calls["selects"] == new_calls["selects"]


def test_generic_history_does_not_treat_bare_month_reports_as_scoped(monkeypatch, dashboard_case):
    months = ["2026-08", "2026-09"]
    selected_ids = dashboard_case["contract_ids"][:2]
    with SessionLocal() as db:
        global_reports = contract_receivables.get_monthly_receivables_batch(db, months)
        assert len(global_reports[months[0]]["rows"]) == 4
        cache = dict(global_reports)
        selected = cached_monthly_report(db, months[0], cache, selected_ids)
        empty = cached_monthly_report(db, months[0], cache, [])
        assert {row["contract_id"] for row in selected["rows"]} == set(selected_ids)
        assert empty["rows"] == []
        assert cached_monthly_report(db, months[0], cache, None) == global_reports[months[0]]

        scoped_history = collect_overdue_history(db, months=months, contract_ids=selected_ids, monthly_reports=dict(global_reports))
        empty_history = collect_overdue_history(db, months=months, contract_ids=[], monthly_reports=dict(global_reports))
        assert set(scoped_history["by_contract"]) == set(selected_ids)
        assert empty_history["by_contract"] == {}
        assert empty_history["total_overdue_balance"] == 0


def test_dashboard_new_request_observes_payments_and_terms_while_frozen_month_stays_fixed(monkeypatch, dashboard_case):
    building_ids = dashboard_case["building_ids"][:1]
    contract_id = dashboard_case["contract_ids"][0]
    before, _ = _read_dashboard(monkeypatch, building_ids)
    with SessionLocal() as db:
        contract = db.get(Contract, contract_id)
        contract.monthly_rent += 100
        contract.rent_terms[0].monthly_amount += 100
        contract.updated_at = datetime(2026, 9, 11, 11)
        db.add(ReceivablePayment(
            contract_id=contract_id, period_month="2026-09", amount=200.15,
            payment_date=TODAY, created_at=datetime(2026, 9, 11, 11),
        ))
        db.commit()

    expected, _ = _read_dashboard(monkeypatch, building_ids, old_month_keys=True)
    after, calls = _read_dashboard(monkeypatch, building_ids)
    assert after == expected
    assert after["current_month"]["receivable"] == before["current_month"]["receivable"] + 100
    assert after["current_month"]["received"] == pytest.approx(before["current_month"]["received"] + 200.15)
    before_august = next(row for row in before["trend"] if row["month"] == "2026-08")
    after_august = next(row for row in after["trend"] if row["month"] == "2026-08")
    assert after_august == before_august
    assert calls["batches"] == 1
    assert calls["months"] == 12
