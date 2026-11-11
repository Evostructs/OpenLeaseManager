from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import (
    Building,
    Contract,
    ContractDeposit,
    ContractRoom,
    Customer,
    CustomerFollowUp,
    ReceivablePayment,
    ReceivableStatement,
    Room,
    Zone,
)
from app.routers.operations import router as operations_router
from app.services.contracts import get_contract_stats
from app.services.dashboard import get_operations_dashboard
from app.services.operating_snapshot import build_operating_snapshot
from app.services.property_scope import SelectedPropertyScope
from app.services.rent_analytics import SEGMENT_KEYS, build_rent_analytics
from app.services.rooms import list_rooms
from app.services.unified_operating_snapshot import build_unified_operating_snapshot


@dataclass(frozen=True)
class _Scope:
    building_ids: tuple[int, ...]


def test_operations_router_exposes_snapshot_endpoint() -> None:
    route = next(
        item for item in operations_router.routes if item.path == "/api/operations/snapshot"
    )
    assert route.methods == {"GET"}


def test_operations_snapshot_uses_stable_authorized_scope_codes() -> None:
    with TestClient(app) as client:
        _register_and_login(client)
        response = client.get(
            "/api/operations/snapshot",
            params={"building": "demo_a", "include_trend": "false"},
        )
        assert response.status_code == 200
        assert response.json()["scope"]["building"]["code"] == "DEMO_A"
        assert client.get(
            "/api/operations/snapshot",
            params={"building": "NOT_AUTHORIZED"},
        ).status_code == 404
        assert client.get(
            "/api/operations/snapshot",
            params={"building_id": 1},
        ).status_code == 422


def _demo_a_scope(db) -> _Scope:
    building = db.query(Building).filter(Building.code == "DEMO_A").one()
    return _Scope(building_ids=(building.id,))


def _register_and_login(client: TestClient) -> None:
    if client.get("/api/auth/status").json()["need_init"]:
        response = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
        )
        assert response.status_code == 200
    response = client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"})
    assert response.status_code == 200


def _seed_snapshot_fixture(client: TestClient, today: date) -> None:
    for room_no, area in (("SNAP-OCC", 100), ("SNAP-VAC", 50)):
        response = client.post(
            "/api/rooms",
            json={
                "tower": "南塔",
                "floor": "12",
                "room_no": room_no,
                "room_type": "办公",
                "area": area,
            },
        )
        assert response.status_code == 200
    customer = client.post("/api/customers", json={"name": "Snapshot fixture customer"})
    assert customer.status_code == 200
    contract = client.post(
        "/api/contracts",
        json={
            "contract_no": "DEMO_A-SNAPSHOT-001",
            "customer_id": customer.json()["id"],
            "tower": "南塔",
            "room_no": "SNAP-OCC",
            "tenant_name": "Snapshot fixture customer",
            "area": 100,
            "unit_price": 4,
            "monthly_rent": 12000,
            "start_date": today.replace(day=1).isoformat(),
            "end_date": (today + timedelta(days=365)).isoformat(),
        },
    )
    assert contract.status_code == 200
    payment = client.post(
        f"/api/contracts/receivables/monthly/{contract.json()['id']}/payments",
        json={
            "month": today.strftime("%Y-%m"),
            "amount": 3000,
            "payment_date": today.isoformat(),
            "allocation_type": "收款",
        },
    )
    assert payment.status_code == 200


def _add_second_building_counterexample(db, today: date) -> Building:
    building = Building(
        code="SNAP_OTHER",
        name="快照隔离反例楼",
        ingestion_mode="detail",
        is_active=True,
    )
    customer = Customer(name="Snapshot other-building customer")
    room = Room(
        building_id=None,
        tower="南塔",
        floor="88",
        room_no="OTHER-OCC",
        room_type="办公",
        area=900,
        is_leasable=True,
    )
    db.add_all([building, customer])
    db.flush()
    zone = Zone(
        building_id=building.id,
        code="SNAP_OTHER_OFFICE",
        name="快照乙楼办公",
        zone_type="office",
        valid_from=today,
        is_active=True,
    )
    db.add(zone)
    db.flush()
    room.building_id = building.id
    room.zone_id = zone.id
    db.add(room)
    db.flush()

    contract = Contract(
        contract_no="OTHER-SNAPSHOT-001",
        customer_id=customer.id,
        building_id=building.id,
        tower="南塔",
        room_no=room.room_no,
        tenant_name=customer.name,
        area=900,
        unit_price=10,
        monthly_rent=90000,
        start_date=today.replace(day=1),
        end_date=today + timedelta(days=20),
    )
    db.add(contract)
    db.flush()
    db.add_all(
        [
            ContractRoom(
                contract_id=contract.id,
                building_id=building.id,
                room_id=room.id,
                tower="南塔",
                room_no=room.room_no,
                area=900,
                unit_price=10,
                monthly_rent=90000,
                start_date=contract.start_date,
                end_date=contract.end_date,
            ),
            ReceivableStatement(
                contract_id=contract.id,
                period_month=today.strftime("%Y-%m"),
                rent=90000,
                property_fee=0,
                total_amount=90000,
                source="manual",
                operator="fixture",
            ),
            ReceivablePayment(
                contract_id=contract.id,
                period_month=today.strftime("%Y-%m"),
                payment_date=today,
                amount=90000,
                allocation_type="收款",
                source="manual",
            ),
            ContractDeposit(
                contract_id=contract.id,
                customer_id=customer.id,
                expected_amount=50000,
                received_amount=0,
                due_date=today - timedelta(days=1),
                status="unpaid",
            ),
            CustomerFollowUp(
                customer_id=customer.id,
                contract_id=contract.id,
                building_id=building.id,
                content="只属于第二楼的逾期跟进",
                next_follow_up_date=today - timedelta(days=1),
                status="open",
            ),
        ]
    )
    db.flush()
    return building


def test_operating_snapshot_preserves_legacy_payloads_and_exposes_raw_facts() -> None:
    today = today_in_timezone()
    with TestClient(app) as client:
        _register_and_login(client)
        _seed_snapshot_fixture(client, today)

    with SessionLocal() as db:
        scope = _demo_a_scope(db)
        expected_contract_stats = get_contract_stats(db)
        expected_operations = get_operations_dashboard(
            db,
            include_trend=True,
            queue_limit=2,
            risk_limit=3,
        )
        snapshot = build_operating_snapshot(
            db,
            scope=scope,
            as_of_date=today,
            include_trend=True,
            queue_limit=2,
            risk_limit=3,
        )

    assert snapshot["contract_version"] == "operating-snapshot-v1"
    assert snapshot["definition_version"] == "OPS_V1"
    assert snapshot["definition_status"] == "published"
    assert snapshot["legacy_contract_stats"] == expected_contract_stats
    assert snapshot["legacy_operations"] == expected_operations

    metrics = snapshot["metrics"]
    occupancy = metrics["occupancy.area_rate"]
    assert occupancy["numerator"] == 100
    assert occupancy["denominator"] == 150
    assert occupancy["value"] == expected_operations["vacancy"]["occupancy_rate"] == 66.7

    collection = metrics["collection.rate_month"]
    assert collection["numerator"] == expected_operations["current_month"]["received"]
    assert collection["denominator"] == expected_operations["current_month"]["receivable"]
    assert collection["value"] == expected_operations["current_month"]["received_rate"]

    required_coverage_states = {
        "data_depth",
        "completeness_status",
        "freshness_status",
        "validation_status",
        "publication_status",
    }
    for metric in metrics.values():
        assert required_coverage_states <= set(metric["coverage"])
        assert metric["definition_version"] == "OPS_V1"
        assert metric["definition_status"] == "published"
        assert metric["definition_hash"]

    tower = {item["name"]: item for item in snapshot["presentation"]["tower"]}
    assert tower["南塔办公"]["total"] == 150
    assert tower["南塔办公"]["leased"] == 100
    assert snapshot["presentation"]["trend"] == expected_operations["trend"]
    assert snapshot["presentation"]["price"]["average_unit_price"] is not None
    assert snapshot["timestamps"]["source_updated_at"] is None
    assert snapshot["timestamps"]["calculated_at"]


def test_demo_a_snapshot_stays_exact_when_second_building_has_business_data() -> None:
    today = today_in_timezone()
    with TestClient(app) as client:
        _register_and_login(client)
        _seed_snapshot_fixture(client, today)

    with SessionLocal() as db:
        scope = _demo_a_scope(db)
        building_ids = scope.building_ids
        dashboard_args = {
            "include_trend": True,
            "queue_limit": 2,
            "risk_limit": 8,
        }
        price_start = (today + relativedelta(months=-6)).replace(day=1)
        baseline_stats = get_contract_stats(db)
        baseline_operations = get_operations_dashboard(db, **dashboard_args)
        baseline_rent = build_rent_analytics(
            db,
            start_date=price_start,
            end_date=today,
            segments=list(SEGMENT_KEYS),
        )
        baseline_rooms = list_rooms(db, include_operations=False)
        baseline_snapshot = build_operating_snapshot(
            db,
            scope=scope,
            as_of_date=today,
            **dashboard_args,
        )

        other = _add_second_building_counterexample(db, today)

        # The counterexample is material: legacy unscoped calls now include it.
        assert get_contract_stats(db)["total"] == baseline_stats["total"] + 1
        assert (
            get_operations_dashboard(db, **dashboard_args)["vacancy"]["total_area"]
            == baseline_operations["vacancy"]["total_area"] + 900
        )

        # Every scoped engine remains byte-for-byte compatible with the DEMO_A-only baseline.
        assert get_contract_stats(db, building_ids=building_ids) == baseline_stats
        assert (
            get_operations_dashboard(db, building_ids=building_ids, **dashboard_args)
            == baseline_operations
        )
        assert build_rent_analytics(
            db,
            start_date=price_start,
            end_date=today,
            segments=list(SEGMENT_KEYS),
            building_ids=building_ids,
        ) == baseline_rent
        assert list_rooms(
            db,
            include_operations=False,
            building_ids=building_ids,
        ) == baseline_rooms

        scoped_snapshot = build_operating_snapshot(
            db,
            scope=scope,
            as_of_date=today,
            **dashboard_args,
        )

    assert other.id not in scoped_snapshot["scope"]["building_ids"]
    assert scoped_snapshot["legacy_contract_stats"] == baseline_snapshot["legacy_contract_stats"]
    assert scoped_snapshot["legacy_operations"] == baseline_snapshot["legacy_operations"]
    assert scoped_snapshot["metrics"] == baseline_snapshot["metrics"]
    assert scoped_snapshot["presentation"] == baseline_snapshot["presentation"]


def test_explicit_empty_building_scope_never_falls_back_to_all_data() -> None:
    today = today_in_timezone()
    with TestClient(app) as client:
        _register_and_login(client)
        _seed_snapshot_fixture(client, today)

    with SessionLocal() as db:
        stats = get_contract_stats(db, building_ids=[])
        operations = get_operations_dashboard(db, building_ids=[])
        rent = build_rent_analytics(
            db,
            start_date=today,
            end_date=today,
            segments=list(SEGMENT_KEYS),
            building_ids=[],
        )
        rooms = list_rooms(db, include_operations=True, building_ids=[])

    assert stats["total"] == 0
    assert stats["metrics"]["room_total_area"] == 0
    assert stats["receivables"]["total_rent"] == 0
    assert operations["vacancy"]["total_rooms"] == 0
    assert operations["current_month"]["receivable"] == 0
    assert operations["queue_counts"] == {
        "overdue_contracts": 0,
        "expiring_contracts": 0,
        "due_follow_up_customers": 0,
        "unpaid_deposits": 0,
        "missing_contract_body": 0,
    }
    assert rent["coverage"]["total_contracts"] == 0
    assert rooms == []


def test_operating_snapshot_uses_null_and_reason_for_unavailable_ratios() -> None:
    today = today_in_timezone()
    with SessionLocal() as db:
        snapshot = build_operating_snapshot(db, scope=_demo_a_scope(db), as_of_date=today)

    metrics = snapshot["metrics"]
    for code in ("occupancy.area_rate", "collection.rate_month", "rent.effective_daily"):
        metric = metrics[code]
        assert metric["value"] is None
        assert metric["unavailable_reason"]
        assert metric["coverage"]["included_buildings"] == 0
        assert metric["coverage"]["completeness_status"] == "missing"

    expiry_pressure = metrics["expiry.area_pressure_90d"]
    assert expiry_pressure["value"] is None
    assert expiry_pressure["numerator"] is None
    assert expiry_pressure["unavailable_reason"] == "现有引擎仅提供到期合同份数，尚无去重到期面积分子"


def test_operating_snapshot_rejects_non_today_and_multi_building_scope() -> None:
    today = today_in_timezone()
    with SessionLocal() as db:
        scope = _demo_a_scope(db)
        with pytest.raises(HTTPException) as historical:
            build_operating_snapshot(db, scope=scope, as_of_date=today - timedelta(days=1))
        assert historical.value.status_code == 422
        assert "不能可靠回算" in str(historical.value.detail)

        with pytest.raises(HTTPException) as multiple:
            build_operating_snapshot(
                db,
                scope=_Scope(building_ids=(scope.building_ids[0], scope.building_ids[0] + 1)),
                as_of_date=today,
            )
        assert multiple.value.status_code == 422
        assert "仅支持单楼范围" in str(multiple.value.detail)


def test_unified_snapshot_excludes_demo_a_live_adapter_for_historical_dates_without_422() -> None:
    today = today_in_timezone()
    with TestClient(app) as client:
        _register_and_login(client)
    with SessionLocal() as db:
        building = db.query(Building).filter(Building.code == "DEMO_A").one()
        scope = SelectedPropertyScope(
            user_id=1,
            building_ids=(building.id,),
            building_codes=(building.code,),
            scope_type="building",
            scope_code=building.code,
            portfolio_id=None,
            access_level="view",
            authorization_version="fixture",
        )
        result = build_unified_operating_snapshot(
            db,
            scope=scope,
            as_of_date=today - timedelta(days=1),
        )

    assert result["definition_version"] == "OPS_V1"
    assert result["adapters"] == [
        {
            "building": {"id": building.id, "code": "DEMO_A", "name": building.name},
            "adapter_type": "live_detail",
            "included": False,
            "reason": "DEMO_A 明细适配器仅支持当天；本次按指定统计日只读取已发布快照",
        }
    ]
    assert result["contributions"][0]["publication_status"] == "missing"
    assert "仅支持当天" in result["contributions"][0]["reason"]
    assert {"legacy_contract_stats", "legacy_operations", "presentation"} <= set(result)


def test_unified_snapshot_uses_generic_detail_adapter_for_registered_detail_building() -> None:
    with TestClient(app) as client:
        _register_and_login(client)

    with SessionLocal() as db:
        building = Building(
            code="UNREGISTERED_DETAIL",
            name="未注册明细楼",
            ingestion_mode="detail",
            is_active=True,
        )
        db.add(building)
        db.flush()
        scope = SelectedPropertyScope(
            user_id=1,
            building_ids=(building.id,),
            building_codes=(building.code,),
            scope_type="building",
            scope_code=building.code,
            portfolio_id=None,
            access_level="view",
            authorization_version="fixture",
        )
        result = build_unified_operating_snapshot(db, scope=scope)

    assert result["adapters"][0]["building"] == {
        "id": building.id,
        "code": "UNREGISTERED_DETAIL",
        "name": "未注册明细楼",
    }
    assert result["adapters"][0]["adapter_type"] == "live_detail"
    assert result["adapters"][0]["included"] is True
    assert result["adapters"][0]["data_classification"] == "simulated"
    assert result["contributions"][0]["publication_status"] == "live_adapter"
    assert result["legacy_operations"]["projection"]["detail_lists_available"] is True
    assert result["legacy_operations"]["projection"]["detail_facts_available"] is False
    assert all(value == 0 for value in result["legacy_operations"]["queue_counts"].values())
    assert result["legacy_operations"]["projection"]["detail_facts_unavailable_reason"]
    for code, metric in result["metrics"].items():
        if code == "portfolio.building_count":
            continue
        assert metric["value"] is None
        assert metric["unavailable_reason"]
