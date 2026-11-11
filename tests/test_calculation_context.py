from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Event, Lock, current_thread

from dateutil.relativedelta import relativedelta
import pytest
from sqlalchemy import event

from app.core.time import today_in_timezone
from app.database import SessionLocal, engine
from app.models import Building, Contract, ContractDeposit, ContractRoom, RentTerm
from app.services import calculation_context, common, contract_rent
from app.services.calculation_context import calculation_scope, reuse_read_calculations
from app.services.common import contract_room_items
from app.services.contract_rent import _month_rent_detail, contract_net_rent
from app.services.contracts import get_contract_stats


JAN = date(2026, 1, 1)
FEB = date(2026, 2, 1)
MAR = date(2026, 3, 1)


def _contract(**overrides) -> Contract:
    values = {
        "id": 101,
        "contract_no": "CALCULATION-101",
        "building_id": 1,
        "tower": "南塔",
        "room_no": "CALC-101",
        "area": 100,
        "monthly_rent": 3100,
        "start_date": date(2026, 1, 15),
        "end_date": date(2026, 3, 10),
        "free_period": 0,
        "room_items": [],
        "rent_terms": [],
    }
    values.update(overrides)
    return Contract(**values)


def _fixed_term_contract(monthly_amount=3100) -> Contract:
    contract = _contract()
    contract.rent_terms = [RentTerm(
        rule_type="fixed_monthly",
        start_date=contract.start_date,
        end_date=contract.end_date,
        monthly_amount=monthly_amount,
    )]
    return contract


def _watch_month_calculations(monkeypatch) -> Counter:
    calls = Counter()
    lock = Lock()
    original = contract_rent._rent_terms_month_detail

    def observed(contract, anchor):
        with lock:
            calls[current_thread().name] += 1
        return original(contract, anchor)

    monkeypatch.setattr(contract_rent, "_rent_terms_month_detail", observed)
    return calls


@pytest.mark.parametrize("raise_inside", [False, True], ids=["normal-return", "exception"])
def test_scope_discards_calculations_after_return_or_exception(monkeypatch, raise_inside):
    contract = _fixed_term_contract()
    calls = _watch_month_calculations(monkeypatch)

    @reuse_read_calculations
    def read():
        assert _month_rent_detail(contract, FEB)["rent"] == 3100
        assert _month_rent_detail(contract, FEB)["rent"] == 3100
        if raise_inside:
            raise ValueError("read interrupted")

    if raise_inside:
        with pytest.raises(ValueError, match="read interrupted"):
            read()
    else:
        read()
    assert sum(calls.values()) == 1

    # A later write must be visible immediately to uncached calculations and
    # to the next reader, including when the preceding reader raised.
    contract.rent_terms[0].monthly_amount = 6200
    assert _month_rent_detail(contract, FEB)["rent"] == 6200
    contract.rent_terms[0].monthly_amount = 9300
    assert _month_rent_detail(contract, FEB)["rent"] == 9300
    with calculation_scope():
        assert _month_rent_detail(contract, FEB)["rent"] == 9300
        assert _month_rent_detail(contract, FEB)["rent"] == 9300
    assert sum(calls.values()) == 4


def test_nested_readers_share_work_without_resetting_the_outer_reader(monkeypatch):
    contract = _fixed_term_contract()
    calls = _watch_month_calculations(monkeypatch)

    @reuse_read_calculations
    def nested_read():
        return _month_rent_detail(contract, FEB)

    @reuse_read_calculations
    def failing_nested_read():
        assert _month_rent_detail(contract, FEB)["rent"] == 3100
        raise ValueError("nested interrupted")

    @reuse_read_calculations
    def outer_read():
        assert _month_rent_detail(contract, FEB) == nested_read()
        with pytest.raises(ValueError, match="nested interrupted"):
            failing_nested_read()
        assert nested_read()["rent"] == 3100

    outer_read()
    assert sum(calls.values()) == 1
    nested_read()
    assert sum(calls.values()) == 2


def test_concurrent_threads_keep_independent_scopes_when_one_reader_exits(monkeypatch):
    contract = _fixed_term_contract()
    calls = _watch_month_calculations(monkeypatch)
    first_ready, second_ready, first_exited = Event(), Event(), Event()

    def first_reader():
        try:
            with calculation_scope():
                assert _month_rent_detail(contract, FEB)["rent"] == 3100
                first_ready.set()
                assert second_ready.wait(10), "second reader did not enter"
                assert _month_rent_detail(contract, FEB)["rent"] == 3100
            first_exited.set()
            assert _month_rent_detail(contract, FEB)["rent"] == 3100
            return current_thread().name
        finally:
            first_ready.set()
            first_exited.set()

    def second_reader():
        assert first_ready.wait(10), "first reader did not enter"
        try:
            with calculation_scope():
                assert _month_rent_detail(contract, FEB)["rent"] == 3100
                second_ready.set()
                assert first_exited.wait(10), "first reader did not exit"
                assert _month_rent_detail(contract, FEB)["rent"] == 3100
            assert _month_rent_detail(contract, FEB)["rent"] == 3100
            return current_thread().name
        finally:
            second_ready.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(first_reader)
        second = executor.submit(second_reader)
        first_name, second_name = first.result(timeout=20), second.result(timeout=20)

    assert first_name != second_name
    # Each thread performs one computation inside its reader and one outside.
    # Reusing another reader's work or clearing its work on exit fails this.
    assert calls == {first_name: 2, second_name: 2}


def test_strategy_clone_with_same_database_id_keeps_its_own_rent_and_rooms():
    formal = _contract(monthly_rent=3100)
    scenario = _contract(monthly_rent=6200, area=200, room_no="SCENARIO-101")
    assert formal.id == scenario.id and formal is not scenario

    with calculation_scope():
        for _ in range(2):
            assert _month_rent_detail(formal, FEB)["rent"] == 3100
            assert _month_rent_detail(scenario, FEB)["rent"] == 6200
            assert contract_room_items(formal)[0]["area"] == 100
            assert contract_room_items(scenario)[0]["area"] == 200
            assert contract_room_items(scenario)[0]["room_no"] == "SCENARIO-101"


def test_observed_new_contract_version_recalculates_month_and_rooms_in_same_scope(monkeypatch):
    contract = _fixed_term_contract()
    contract.updated_at = datetime(2026, 1, 1, 12)
    calls = _watch_month_calculations(monkeypatch)
    with calculation_scope():
        assert _month_rent_detail(contract, FEB)["rent"] == 3100
        assert contract_room_items(contract)[0]["area"] == 100

        # A populate_existing refresh can update the same ORM object's fields.
        # Once the reader observes the new version, prior figures must expire.
        contract.rent_terms[0].monthly_amount = 6200
        contract.area = 200
        contract.updated_at += timedelta(seconds=1)

        for _ in range(2):
            assert _month_rent_detail(contract, FEB)["rent"] == 6200
            assert contract_room_items(contract)[0]["area"] == 200
    assert sum(calls.values()) == 2


def test_exhausted_calculation_budget_preserves_results_and_existing_cached_work(monkeypatch):
    contract = _fixed_term_contract()
    calls = _watch_month_calculations(monkeypatch)
    monkeypatch.setattr(calculation_context, "MAX_CALCULATIONS", 1)

    with calculation_scope():
        assert _month_rent_detail(contract, FEB)["rent"] == 3100
        assert _month_rent_detail(contract, FEB)["rent"] == 3100
        assert sum(calls.values()) == 1

        # A month that no longer fits must remain correct on every uncached read.
        assert _month_rent_detail(contract, JAN)["rent"] == 1700
        assert _month_rent_detail(contract, JAN)["rent"] == 1700
        assert sum(calls.values()) == 3
        assert _month_rent_detail(contract, FEB)["rent"] == 3100
        assert sum(calls.values()) == 3


def test_multiroom_result_consumes_budget_by_rows_and_can_fall_back_without_errors(monkeypatch):
    contract, _ = _boundary_case("multiple-room-periods")
    expected = contract_room_items(contract)
    calls = 0
    original = common.contract_room_effective_period

    def observed(contract, item):
        nonlocal calls
        calls += 1
        return original(contract, item)

    monkeypatch.setattr(common, "contract_room_effective_period", observed)
    monkeypatch.setattr(calculation_context, "MAX_CALCULATIONS", 1)
    with calculation_scope():
        assert contract_room_items(contract) == expected
        first_calls = calls
        assert first_calls > 0
        assert contract_room_items(contract) == expected
        assert calls == 2 * first_calls

        # The oversized result did not consume the room needed by a smaller
        # one-row result, which can still be reused within this operation.
        january = contract_room_items(contract, JAN, date(2026, 1, 31))
        after_january = calls
        assert [row["room_no"] for row in january] == ["A"]
        assert contract_room_items(contract, JAN, date(2026, 1, 31)) == january
        assert calls == after_january


def test_modifying_returned_month_detail_or_room_list_does_not_poison_future_reads():
    contract = _contract()
    expected_detail = _month_rent_detail(contract, FEB)
    expected_rooms = contract_room_items(contract)

    with calculation_scope():
        # Check ownership both for the initial result and subsequent cache hits.
        for _ in range(2):
            detail = _month_rent_detail(contract, FEB)
            rooms = contract_room_items(contract)
            assert detail == expected_detail
            assert rooms == expected_rooms
            detail["rent"] = -999
            detail["caller_annotation"] = "local presentation only"
            rooms[0]["area"] = -999
            rooms[0]["caller_annotation"] = "local presentation only"
            rooms.append({"room_no": "synthetic"})
            assert _month_rent_detail(contract, FEB) == expected_detail
            assert contract_room_items(contract) == expected_rooms


def _boundary_case(name):
    contract = _contract()
    expected = [1700, 3100, 1000]
    if name in {"rolling-free", "rolling-free-with-base-term"}:
        contract.start_date = date(2026, 1, 25)
        contract.monthly_rent = 2800
        contract.free_period = 1
        expected = [0, 400, 903.23]
        if name == "rolling-free-with-base-term":
            contract.rent_terms = [RentTerm(
                rule_type="fixed_monthly", start_date=contract.start_date,
                end_date=contract.end_date, monthly_amount=2800,
            )]
    elif name == "multiple-room-periods":
        contract.room_items = [
            ContractRoom(id=1, room_no="A", tower="南塔", area=100,
                         monthly_rent=3100, start_date=contract.start_date,
                         end_date=contract.end_date),
            ContractRoom(id=2, room_no="B", tower="北塔", area=80,
                         monthly_rent=2800, start_date=date(2026, 2, 10),
                         end_date=date(2026, 2, 20)),
        ]
        expected = [1700, 4200, 1000]
    elif name == "stepped-free-escalation-adjustment":
        contract.rent_terms = [
            RentTerm(rule_type="fixed_monthly", start_date=contract.start_date,
                     end_date=date(2026, 2, 14), monthly_amount=3100),
            RentTerm(rule_type="stepped_rent", start_date=date(2026, 2, 15),
                     end_date=contract.end_date, monthly_amount=6200),
            RentTerm(rule_type="rent_free", start_date=date(2026, 1, 20),
                     end_date=date(2026, 1, 22)),
            RentTerm(rule_type="escalation", start_date=FEB,
                     end_date=contract.end_date, adjustment_type="percent",
                     adjustment_value=10, frequency="once"),
            RentTerm(rule_type="temporary_adjustment", start_date=date(2026, 2, 25),
                     end_date=date(2026, 2, 28), adjustment_type="fixed_amount",
                     adjustment_value=280),
        ]
        expected = [1400, 5075, 2200]
    return contract, expected


@pytest.mark.parametrize("case", [
    "partial-first-and-last-month", "rolling-free", "rolling-free-with-base-term",
    "multiple-room-periods", "stepped-free-escalation-adjustment",
])
def test_cached_rent_matches_uncached_billing_and_known_boundary_amounts(case):
    contract, expected_rents = _boundary_case(case)
    months = (JAN, FEB, MAR)
    uncached = {month: _month_rent_detail(contract, month) for month in months}
    uncached_net_rent = contract_net_rent(contract)
    assert [uncached[month]["rent"] for month in months] == expected_rents

    with calculation_scope():
        for month in months:
            assert _month_rent_detail(contract, month) == uncached[month]
        # Net rent aggregates these same monthly results. Reversed repeat reads
        # also detect a cache key that accidentally ignores the month argument.
        assert contract_net_rent(contract) == uncached_net_rent
        for month in reversed(months):
            assert _month_rent_detail(contract, month) == uncached[month]
        assert _month_rent_detail(contract, date(2025, 12, 1))["rent"] == 0
        assert _month_rent_detail(contract, date(2026, 4, 1))["rent"] == 0


def test_room_window_arguments_keep_inactive_rooms_out_of_month_and_day_reads():
    contract, _ = _boundary_case("multiple-room-periods")
    with calculation_scope():
        assert [row["room_no"] for row in contract_room_items(contract, JAN, date(2026, 1, 31))] == ["A"]
        assert [row["room_no"] for row in contract_room_items(contract, FEB, date(2026, 2, 28))] == ["A", "B"]
        assert [row["room_no"] for row in contract_room_items(contract, window_start=date(2026, 2, 21))] == ["A"]
        assert contract_room_items(contract, window_end=date(2026, 3, 11)) == []
        assert [row["room_no"] for row in contract_room_items(contract, JAN, date(2026, 1, 31))] == ["A"]


def _seed_deposit_contracts(start, stop, building_id):
    today = today_in_timezone()
    with SessionLocal() as db:
        for index in range(start, stop):
            contract = _contract(
                id=None, contract_no=f"CALC-DEPOSIT-{index}", building_id=building_id,
                room_no=f"CALC-DEPOSIT-{index}", tenant_name=f"Deposit tenant {index}",
                start_date=today.replace(day=1),
                end_date=today.replace(day=1) + relativedelta(months=2, days=-1),
                deposit=9000, deposit_status="已收", is_deleted=False,
            )
            variant = index % 5
            if variant == 0:
                contract.deposit = 1250.25
            elif variant == 1:
                contract.deposit = 240.10
                contract.deposit_status = ""
            elif variant == 2:
                contract.deposit_status = "未收"
            elif variant == 3:
                contract.deposits = [
                    ContractDeposit(expected_amount=2000, received_amount=1200.10,
                                    refunded_amount=150.05, offset_amount=100.05),
                    ContractDeposit(expected_amount=200, received_amount=200,
                                    refunded_amount=20, offset_amount=0),
                ]
            else:
                # An explicit unpaid ledger must suppress the old paid field.
                contract.deposits = [ContractDeposit(
                    expected_amount=9000, received_amount=0, refunded_amount=0,
                    offset_amount=0,
                )]
            db.add(contract)
        db.commit()


def _read_stats_with_deposit_query_count(building_ids):
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select ") and "from contract_deposits " in normalized:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        # A new session excludes identity-map warming from setup or earlier reads.
        with SessionLocal() as db:
            result = get_contract_stats(db, building_ids=building_ids)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    return result, len(statements)


@pytest.mark.parametrize("scoped", [False, True], ids=["legacy-global", "building-scoped"])
def test_stats_deposit_queries_stay_bounded_and_legacy_and_ledger_totals_survive(scoped):
    with SessionLocal() as db:
        building_id = db.query(Building.id).filter(Building.code == "DEMO_A").scalar()
    assert building_id is not None
    building_ids = [building_id] if scoped else None

    _seed_deposit_contracts(0, 1, building_id)
    single, single_queries = _read_stats_with_deposit_query_count(building_ids)
    _seed_deposit_contracts(1, 20, building_id)
    many, many_queries = _read_stats_with_deposit_query_count(building_ids)

    assert single["total"] == 1
    assert single["metrics"]["total_deposit"] == 1250.25
    assert many["total"] == 20
    # Four repetitions of: paid legacy + blank legacy + unpaid legacy +
    # (two ledger balances after refunds/offsets) + explicit unpaid ledger.
    assert many["metrics"]["total_deposit"] == 10481.40
    assert single_queries > 0, "query capture must observe the actual ledger read"
    assert many_queries <= single_queries + 1
    assert many_queries <= 2, "deposit loading regressed to one query per contract"

    # A second request must observe new receipts, rather than retaining query
    # results or financial aggregates from the previous read scope.
    with SessionLocal() as db:
        deposit = db.query(ContractDeposit).join(Contract).filter(
            Contract.contract_no == "CALC-DEPOSIT-4"
        ).one()
        deposit.received_amount = 100
        db.commit()
    refreshed, _ = _read_stats_with_deposit_query_count(building_ids)
    assert refreshed["metrics"]["total_deposit"] == 10581.40
