from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime, time, timedelta

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
import pytest
from sqlalchemy import event
from sqlalchemy.orm.attributes import set_committed_value

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.models import (
    Building, Contract, ContractRoom, Portfolio, ReceivablePayment,
    ReceivableStatement, RentTerm, Room, User, Zone,
)
from app.services import (
    building_operating_gate, operating_snapshot, rent_analytics,
    unified_operating_snapshot,
)
from app.services.metric_definitions import ensure_ops_v1_definitions
from app.services.portfolio_overview import build_portfolio_overview
from app.services.property_scope import SelectedPropertyScope
from app.services.unified_operating_snapshot import build_unified_operating_snapshot


@pytest.fixture
def rent_projection_case(monkeypatch):
    today = today_in_timezone()
    stamp = datetime.combine(today, time(12))
    monkeypatch.setattr(operating_snapshot, "now_in_timezone", lambda: stamp)
    monkeypatch.setattr(building_operating_gate, "now_in_timezone", lambda: stamp)
    start = today + relativedelta(years=-2)
    end = today + relativedelta(years=1)
    result = {"today": today, "start": (today + relativedelta(months=-6)).replace(day=1)}
    building_ids, contract_ids, room_ids = [], [], []
    with SessionLocal() as db:
        actor = User(
            username="rent-projection-actor", display_name="租金投影测试人",
            password_hash="test-only", role="admin", is_active=True,
        )
        db.add(actor)
        db.flush()
        ensure_ops_v1_definitions(db, publisher=actor)
        result["user_id"] = actor.id
        for code in ("DEMO_A", "DEMO_C"):
            building = db.query(Building).filter(Building.code == code).one()
            building.ingestion_mode = "detail"
            building_ids.append(building.id)
            zone = db.query(Zone).filter(Zone.building_id == building.id).order_by(Zone.id).first()
            if zone is None:
                zone = Zone(
                    building_id=building.id, code="WHOLE_BUILDING", name="整楼",
                    zone_type="whole_building", valid_from=start, is_active=True,
                )
                db.add(zone)
                db.flush()
            tower = "南塔" if code == "DEMO_A" else "整楼"

            def add_contract(suffix, *, area=100.123456, rent=3100.17,
                             contract_start=start, disposal="", with_terms=False):
                room = Room(
                    building_id=building.id, zone_id=zone.id, tower=tower, floor="12",
                    room_no=f"PROJ-{code}-{suffix}", room_type="办公", area=area,
                    is_leasable=True, is_deleted=False,
                )
                db.add(room)
                db.flush()
                contract = Contract(
                    building_id=building.id, contract_no=room.room_no,
                    tenant_name=f"Projection {code} {suffix}", tower=tower,
                    room_no=room.room_no, area=area, monthly_rent=rent, unit_price=0,
                    start_date=contract_start, end_date=end, disposal=disposal,
                    property_fee_rate=2.5, payment_due_day=5,
                )
                contract.room_items = [ContractRoom(
                    building_id=building.id, room_id=room.id, room_no=room.room_no,
                    tower=tower, area=area, monthly_rent=rent, unit_price=0,
                    start_date=start, end_date=end,
                )]
                if with_terms:
                    contract.rent_terms = [
                        RentTerm(rule_type="fixed_monthly", start_date=start,
                                 end_date=end, monthly_amount=rent),
                        RentTerm(rule_type="rent_free", start_date=today - timedelta(days=4),
                                 end_date=today - timedelta(days=3)),
                        RentTerm(rule_type="temporary_adjustment",
                                 start_date=today - timedelta(days=2),
                                 end_date=today - timedelta(days=1),
                                 adjustment_type="fixed_amount", adjustment_value=31.25),
                        RentTerm(rule_type="escalation", start_date=today,
                                 end_date=end, adjustment_type="percent",
                                 adjustment_value=5, frequency="annual"),
                    ]
                db.add(contract)
                db.flush()
                return contract, room

            good, _ = add_contract("VALID", with_terms=True)
            contract_ids.append(good.id)
            missing, missing_room = add_contract("MISSING-DATE", contract_start=None)
            room_ids.append(missing_room.id)
            result[f"missing-{code}"] = missing.id
            add_contract("ZERO-AREA", area=0)
            add_contract("NO-RENT", rent=0)
            add_contract("NO-EXIT-DATE", disposal="已退租")
            db.add_all([
                ReceivableStatement(
                    contract_id=good.id, period_month=(today + relativedelta(months=-1)).strftime("%Y-%m"),
                    rent=2222.22, property_fee=250.01, total_amount=2472.23,
                    source="manual", generated_at=stamp,
                ),
                ReceivablePayment(
                    contract_id=good.id, period_month=today.strftime("%Y-%m"),
                    amount=333.37, payment_date=today, created_at=stamp,
                ),
            ])
        portfolio = db.query(Portfolio).filter(Portfolio.code == "DEMO_OFFICE_PORTFOLIO").one()
        result.update(portfolio_id=portfolio.id, portfolio_code=portfolio.code)
        db.commit()
    result.update(building_ids=tuple(building_ids), contract_ids=tuple(contract_ids), room_ids=tuple(room_ids))
    return result


def _rent_args(case, building_ids):
    return dict(start_date=case["start"], end_date=case["today"],
                segments=list(rent_analytics.SEGMENT_KEYS), building_ids=building_ids)


def _scope(case, kind):
    portfolio = kind in {"portfolio", "overview"}
    generic = kind == "generic"
    return SelectedPropertyScope(
        user_id=case["user_id"], building_ids=case["building_ids"] if portfolio else (case["building_ids"][int(generic)],),
        building_codes=("DEMO_A", "DEMO_C") if portfolio else (("DEMO_C",) if generic else ("DEMO_A",)),
        scope_type="portfolio" if portfolio else "building",
        scope_code=case["portfolio_code"] if portfolio else ("DEMO_C" if generic else "DEMO_A"),
        portfolio_id=case["portfolio_id"] if portfolio else None,
        access_level="view", authorization_version="rent-projection-fixture",
    )


@pytest.mark.parametrize("scope_kind", ["single", "generic", "empty", "global"])
def test_projection_matches_complete_rent_fields_including_rounding_and_exclusions(rent_projection_case, scope_kind):
    case = rent_projection_case
    ids = {"single": case["building_ids"][:1], "generic": case["building_ids"][1:],
           "empty": [], "global": None}[scope_kind]
    args = _rent_args(case, ids)
    with SessionLocal() as db:
        full = rent_analytics.build_rent_analytics(db, **args)
    with SessionLocal() as db:
        projection = rent_analytics.build_snapshot_rent_projection(db, **args)
    assert projection == {key: full[key] for key in ("segments", "coverage", "daily")}
    assert len(projection["daily"]) == (case["today"] - case["start"]).days + 1
    if scope_kind == "empty":
        assert projection["coverage"]["total_contracts"] == 0
        assert all(row["segments"]["overall"]["area"] == 0 for row in projection["daily"])
        return
    excluded = projection["coverage"]["excluded"]
    assert len(excluded) == (8 if scope_kind == "global" else 4)
    assert {reason for item in excluded for reason in item["reasons"]} == {
        "缺少合同起止日期", "所选期间缺少有效出租面积", "缺少可计算的租金依据",
        "已退租但未登记实际退租日期",
    }
    assert all(set(item) == {"contract_id", "contract_no", "tenant_name", "start_date", "end_date", "reasons"}
               for item in excluded)
    for row in projection["daily"]:
        for metric in row["segments"].values():
            assert metric["rent"] == round(metric["rent"], 6)
            assert metric["area"] == round(metric["area"], 4)
    if scope_kind == "generic":
        assert projection["segments"] == [{"key": "overall", "label": "整楼", "color": "#1f6feb"}]
    if scope_kind in {"single", "generic"}:
        today_row = projection["daily"][-1]["segments"]["overall"]
        assert today_row["area"] == round(100.123456, 4)
        free_row = next(row for row in projection["daily"] if row["date"] == (case["today"] - timedelta(days=4)).isoformat())
        assert free_row["segments"]["overall"]["rent"] == 0
        assert free_row["segments"]["overall"]["area"] == today_row["area"]


@pytest.mark.parametrize("kind", ["single", "generic", "portfolio", "overview"])
def test_complete_snapshot_and_overview_match_previous_full_rent_boundary(monkeypatch, rent_projection_case, kind):
    case = rent_projection_case
    scope = _scope(case, kind)
    read = build_portfolio_overview if kind == "overview" else build_unified_operating_snapshot
    # Use the old complete analysis boundary, with independent ORM sessions.
    with monkeypatch.context() as patch:
        patch.setattr(operating_snapshot, "build_snapshot_rent_projection", rent_analytics.build_rent_analytics, raising=False)
        with SessionLocal() as db:
            expected = read(db, scope=scope, as_of_date=case["today"])
    with SessionLocal() as db:
        actual = read(db, scope=scope, as_of_date=case["today"])
    assert actual == expected  # All business fields, sources, frozen amounts, lists and prices.
    if kind in {"single", "generic"}:
        assert actual["legacy_contract_stats"]["total"] == 5
        assert actual["presentation"]["price"]["coverage"]["excluded_contracts"] == 4
    else:
        assert actual["coverage"]["live_adapter_buildings"] == 2


@pytest.mark.parametrize("read_kind", ["projection", "snapshot"])
def test_snapshot_rent_path_does_not_build_unused_analysis(monkeypatch, rent_projection_case, read_kind):
    case = rent_projection_case
    calls = Counter()
    for name in ("_calculate_daily", "_summary_for_rows", "_comparison", "_build_events"):
        original = getattr(rent_analytics, name)
        def counted(*args, _name=name, _original=original, **kwargs):
            calls[_name] += 1
            return _original(*args, **kwargs)
        monkeypatch.setattr(rent_analytics, name, counted)
    with SessionLocal() as db:
        rent_analytics.build_rent_analytics(db, **_rent_args(case, case["building_ids"][:1]))
    assert calls["_summary_for_rows"] >= 1
    assert calls["_comparison"] == 2
    assert calls["_build_events"] == 1
    calls.clear()
    with SessionLocal() as db:
        if read_kind == "projection":
            rent_analytics.build_snapshot_rent_projection(db, **_rent_args(case, case["building_ids"][:1]))
        else:
            operating_snapshot.build_operating_snapshot(db, scope=_scope(case, "single"), as_of_date=case["today"])
    assert calls == {"_calculate_daily": 1}


@pytest.mark.parametrize("bad_input", ["multiple_buildings", "invalid_segment", "date_order"])
def test_projection_preserves_full_analysis_validation(rent_projection_case, bad_input):
    case = rent_projection_case
    args = _rent_args(case, case["building_ids"][:1])
    if bad_input == "multiple_buildings":
        args["building_ids"] = case["building_ids"]
    elif bad_input == "invalid_segment":
        args["segments"] = ["unknown"]
    else:
        args["start_date"] = case["today"] + timedelta(days=1)
    errors = []
    for read in (rent_analytics.build_rent_analytics, rent_analytics.build_snapshot_rent_projection):
        with SessionLocal() as db, pytest.raises(HTTPException) as raised:
            read(db, **args)
        errors.append((raised.value.status_code, raised.value.detail))
    assert errors[0] == errors[1]
    assert errors[0][0] == 422


@pytest.mark.parametrize("corruption", ["contract_room_building", "linked_room_building", "deleted_room"])
@pytest.mark.parametrize("kind", ["single", "portfolio"])
@pytest.mark.parametrize("preload_empty_relation", [False, True])
def test_snapshot_still_rejects_bad_room_graph_for_rent_excluded_contract(
    rent_projection_case, corruption, kind, preload_empty_relation,
):
    case = rent_projection_case
    injected = []
    # Inject an invalid legacy read in this Session only; never weaken database
    # constraints or persist corrupt rows. The affected contract lacks dates and
    # would be excluded by rent analytics, but the full graph guard must reject it.
    def inject_bad_legacy_graph(_session, instance):
        if corruption == "contract_room_building":
            if isinstance(instance, ContractRoom) and instance.contract_id == case["missing-DEMO_A"]:
                set_committed_value(instance, "building_id", case["building_ids"][1])
                injected.append(instance.id)
        elif isinstance(instance, Room) and instance.id == case["room_ids"][0]:
            key, value = ("is_deleted", True) if corruption == "deleted_room" else ("building_id", case["building_ids"][1])
            set_committed_value(instance, key, value)
            injected.append(instance.id)
    with SessionLocal() as db:
        event.listen(db, "loaded_as_persistent", inject_bad_legacy_graph)
        try:
            if preload_empty_relation:
                contract = db.get(Contract, case["missing-DEMO_A"])
                set_committed_value(contract, "room_items", [])
            with pytest.raises(HTTPException) as raised:
                build_unified_operating_snapshot(db, scope=_scope(case, kind), as_of_date=case["today"])
            assert raised.value.status_code == 409
            assert "楼宇不一致" in raised.value.detail
            assert injected
            assert not db.dirty
        finally:
            event.remove(db, "loaded_as_persistent", inject_bad_legacy_graph)


def test_contract_summary_does_not_check_room_graphs_outside_selected_scope(
    monkeypatch, rent_projection_case,
):
    case = rent_projection_case
    scope = replace(
        _scope(case, "portfolio"), building_ids=case["building_ids"][:1],
        building_codes=("DEMO_A",),
    )
    injected = []

    def inject_out_of_scope_graph(_session, instance):
        if isinstance(instance, ContractRoom) and instance.contract_id == case["missing-DEMO_C"]:
            set_committed_value(instance, "building_id", case["building_ids"][0])
            injected.append(instance.id)

    outputs = []
    for use_full_stats in (True, False):
        with monkeypatch.context() as patch:
            if use_full_stats:
                patch.setattr(operating_snapshot, "get_contract_summary", operating_snapshot.get_contract_stats)
            with SessionLocal() as db:
                event.listen(db, "loaded_as_persistent", inject_out_of_scope_graph)
                try:
                    # Keep a known bad out-of-scope row present in the identity
                    # map; neither route may broaden the graph guard to it.
                    outside = db.query(ContractRoom).filter(
                        ContractRoom.contract_id == case["missing-DEMO_C"],
                    ).one()
                    outputs.append(build_unified_operating_snapshot(
                        db, scope=scope, as_of_date=case["today"], include_trend=False,
                    ))
                    assert outside.building_id == case["building_ids"][0]
                    assert not db.dirty
                finally:
                    event.remove(db, "loaded_as_persistent", inject_out_of_scope_graph)
    assert injected
    assert outputs[0] == outputs[1]
    assert outputs[1]["scope"]["building_ids"] == list(case["building_ids"][:1])


@pytest.mark.parametrize("kind", ["single", "generic", "portfolio", "overview"])
def test_contract_summary_preserves_complete_response_and_single_building_stats(
    monkeypatch, rent_projection_case, kind,
):
    case = rent_projection_case
    read = build_portfolio_overview if kind == "overview" else build_unified_operating_snapshot
    scope = _scope(case, kind)
    # Compare through the real aggregation entry point, using the former full
    # statistics boundary as the reference and separate ORM sessions.
    with monkeypatch.context() as patch:
        patch.setattr(operating_snapshot, "get_contract_summary", operating_snapshot.get_contract_stats)
        with SessionLocal() as db:
            expected = read(db, scope=scope, as_of_date=case["today"])

    calls = Counter()
    for name in ("get_contract_stats", "get_contract_summary"):
        original = getattr(operating_snapshot, name)
        def counted(*args, _name=name, _original=original, **kwargs):
            calls[_name] += 1
            return _original(*args, **kwargs)
        monkeypatch.setattr(operating_snapshot, name, counted)
    with SessionLocal() as db:
        actual = read(db, scope=scope, as_of_date=case["today"])
    assert actual == expected
    if kind in {"single", "generic"}:
        assert calls == {"get_contract_stats": 1}
        stats = actual["legacy_contract_stats"]
        assert stats["total"] == 5
        assert {"customer_data", "tower_data", "income_forecast", "receivables", "metrics_trends"} <= set(stats)
        assert len(stats["income_forecast"]) == 12
        assert len(stats["receivables"]["trend"]) == 6
    else:
        assert calls == {"get_contract_summary": 2}
        assert actual["coverage"]["live_adapter_buildings"] == 2


@pytest.mark.parametrize("inventory", ["empty", "rooms_only", "contract_only"])
def test_portfolio_contract_summary_keeps_empty_detail_semantics(
    monkeypatch, rent_projection_case, inventory,
):
    case = rent_projection_case
    generic_id = case["building_ids"][1]
    with SessionLocal() as db:
        # Soft-delete fixture data only; an unlinked, unpriced legacy contract
        # distinguishes contract presence from usable room/rent inventory.
        for contract in db.query(Contract).filter(Contract.building_id == generic_id):
            contract.is_deleted = True
        if inventory != "rooms_only":
            for room in db.query(Room).filter(Room.building_id == generic_id):
                room.is_deleted = True
        if inventory == "contract_only":
            db.add(Contract(
                building_id=generic_id, contract_no="SUMMARY-UNLINKED",
                tenant_name="无房源明细合同", tower="整楼", area=0, monthly_rent=0,
            ))
        db.commit()

    with monkeypatch.context() as patch:
        patch.setattr(operating_snapshot, "get_contract_summary", operating_snapshot.get_contract_stats)
        with SessionLocal() as db:
            expected = build_unified_operating_snapshot(
                db, scope=_scope(case, "portfolio"), as_of_date=case["today"], include_trend=False,
            )
    with SessionLocal() as db:
        actual = build_unified_operating_snapshot(
            db, scope=_scope(case, "portfolio"), as_of_date=case["today"], include_trend=False,
        )
    assert actual == expected
    contribution = next(item for item in actual["contributions"] if item["building"]["id"] == generic_id)
    metric = contribution["metrics"]["billing.receivable_month"]
    if inventory == "empty":
        assert metric["value"] is None
        assert "明细源为空" in metric["unavailable_reason"]
    else:
        assert metric["value"] == 0
        assert metric["unavailable_reason"] is None


@pytest.mark.parametrize("corruption", ["date", "definition", "building", "missing_source", "source_reference"])
def test_contract_summary_preserves_real_adapter_validation(
    monkeypatch, rent_projection_case, corruption,
):
    case = rent_projection_case
    original = unified_operating_snapshot.build_operating_snapshot

    def altered_detail(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        if corruption == "date":
            snapshot["as_of_date"] = (case["today"] - timedelta(days=1)).isoformat()
        elif corruption == "definition":
            snapshot["definition_version"] = "INVALID"
        elif corruption == "building":
            snapshot["scope"]["building_ids"] = [0]
        elif corruption == "missing_source":
            snapshot["sources"] = []
        else:
            snapshot["metrics"]["occupancy.area_rate"]["coverage"]["source_ids"] = ["undeclared-source"]
        return snapshot

    monkeypatch.setattr(unified_operating_snapshot, "build_operating_snapshot", altered_detail)
    errors = []
    for use_full_stats in (True, False):
        with monkeypatch.context() as patch:
            if use_full_stats:
                patch.setattr(operating_snapshot, "get_contract_summary", operating_snapshot.get_contract_stats)
            with SessionLocal() as db, pytest.raises(HTTPException) as raised:
                build_unified_operating_snapshot(
                    db, scope=_scope(case, "portfolio"), as_of_date=case["today"], include_trend=False,
                )
            errors.append((raised.value.status_code, raised.value.detail))
    assert errors[0] == errors[1]
    assert errors[0][0] == 409
