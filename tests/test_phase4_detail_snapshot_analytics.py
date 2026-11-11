from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import io

import openpyxl
import pytest
from fastapi import HTTPException

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.models import Building, Contract, ContractRoom, Room, Zone
from app.services.contract_rent import calculate_monthly_rent
from app.services.operating_snapshot import build_operating_snapshot
from app.services.rent_analytics import (
    SEGMENT_KEYS,
    build_rent_analytics,
    export_rent_analytics_excel,
)


@dataclass(frozen=True)
class _Scope:
    building_ids: tuple[int, ...]


async def _response_bytes(response) -> bytes:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode())
    return b"".join(chunks)


def _activate_detail_pilot(db, *, with_business_data: bool) -> Building:
    today = today_in_timezone()
    building = db.query(Building).filter(Building.code == "DEMO_C").one()
    building.ingestion_mode = "detail"
    zone = Zone(
        building_id=building.id,
        code="WHOLE_BUILDING",
        name="整楼",
        zone_type="whole_building",
        valid_from=today,
        is_active=True,
    )
    db.add(zone)
    db.flush()
    if with_business_data:
        occupied = Room(
            building_id=building.id,
            zone_id=zone.id,
            tower="整楼",
            floor="",
            room_no="XSH-OCC",
            room_type="办公",
            area=120,
            is_leasable=True,
        )
        vacant = Room(
            building_id=building.id,
            zone_id=zone.id,
            tower="整楼",
            floor="",
            room_no="XSH-VAC",
            room_type="办公",
            area=80,
            is_leasable=True,
        )
        db.add_all([occupied, vacant])
        db.flush()
        contract = Contract(
            building_id=building.id,
            contract_no="DEMO_C-2026-001",
            tower="整楼",
            room_no=occupied.room_no,
            tenant_name="Phase 4 模拟租户",
            area=120,
            unit_price=4,
            monthly_rent=calculate_monthly_rent(120, 4),
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=365),
        )
        db.add(contract)
        db.flush()
        db.add(
            ContractRoom(
                contract_id=contract.id,
                building_id=building.id,
                room_id=occupied.id,
                tower="整楼",
                room_no=occupied.room_no,
                area=120,
                unit_price=4,
                monthly_rent=calculate_monthly_rent(120, 4),
                start_date=contract.start_date,
                end_date=contract.end_date,
            )
        )
    db.commit()
    db.refresh(building)
    return building


def _seed_demo_a_counterexample(db) -> None:
    today = today_in_timezone()
    building = db.query(Building).filter(Building.code == "DEMO_A").one()
    zone = db.query(Zone).filter(Zone.building_id == building.id).order_by(Zone.id).first()
    room = Room(
        building_id=building.id,
        zone_id=zone.id,
        tower="南塔",
        floor="88",
        room_no="DEMO_A-PHASE4-COUNTEREXAMPLE",
        room_type="办公",
        area=900,
        is_leasable=True,
    )
    db.add(room)
    db.flush()
    contract = Contract(
        building_id=building.id,
        contract_no="DEMO_A-PHASE4-COUNTEREXAMPLE",
        tower="南塔",
        room_no=room.room_no,
        tenant_name="不应进入新上海指标",
        area=900,
        unit_price=10,
        monthly_rent=calculate_monthly_rent(900, 10),
        start_date=today - timedelta(days=30),
        end_date=today + timedelta(days=365),
    )
    db.add(contract)
    db.flush()
    db.add(
        ContractRoom(
            contract_id=contract.id,
            building_id=building.id,
            room_id=room.id,
            tower="南塔",
            room_no=room.room_no,
            area=900,
            unit_price=10,
            monthly_rent=calculate_monthly_rent(900, 10),
            start_date=contract.start_date,
            end_date=contract.end_date,
        )
    )
    db.commit()


def test_live_detail_snapshot_uses_selected_building_identity_and_source() -> None:
    with SessionLocal() as db:
        building = _activate_detail_pilot(db, with_business_data=True)
        _seed_demo_a_counterexample(db)

        snapshot = build_operating_snapshot(
            db,
            scope=_Scope(building_ids=(int(building.id),)),
            include_trend=False,
        )
        analytics = build_rent_analytics(
            db,
            start_date=today_in_timezone(),
            end_date=today_in_timezone(),
            building_ids=(int(building.id),),
        )
        export_response = export_rent_analytics_excel(analytics, list(SEGMENT_KEYS))

    assert snapshot["scope"]["building"] == {
        "id": building.id,
        "code": "DEMO_C",
        "name": "示例单栋中心",
        "timezone": building.timezone,
        "currency": building.currency,
        "ingestion_mode": "detail",
    }
    assert snapshot["scope"]["building_ids"] == [building.id]
    assert snapshot["sources"] == [
        {
            "id": "demo-c-detail-live",
            "name": "示例单栋中心 现有租赁明细库",
            "source_type": "detail",
            "source_observed_at": None,
            "source_updated_at": None,
        }
    ]
    assert all(
        metric["coverage"]["source_ids"] in ([], ["demo-c-detail-live"])
        for metric in snapshot["metrics"].values()
    )
    assert "DEMO_A" not in str(snapshot)
    assert snapshot["legacy_contract_stats"]["total"] == 1
    assert snapshot["metrics"]["area.rentable_sqm"]["value"] == 200
    assert snapshot["metrics"]["area.leased_sqm"]["value"] == 120
    assert snapshot["metrics"]["occupancy.area_rate"]["value"] == 60
    assert snapshot["metrics"]["rent.effective_daily"]["value"] is not None
    assert snapshot["presentation"]["tower"] == [
        {"name": "整楼", "total": 200, "leased": 120, "rate": 60}
    ]
    assert set(snapshot["presentation"]["price"]["current_by_segment"]) == {"overall"}
    assert snapshot["presentation"]["price"]["segments"] == [
        {"key": "overall", "label": "整楼", "color": "#1f6feb"}
    ]
    assert analytics["segments"] == [
        {"key": "overall", "label": "整楼", "color": "#1f6feb"}
    ]
    assert set(analytics["daily"][0]["segments"]) == {"overall"}
    assert analytics["daily"][0]["segments"]["overall"]["area"] == 120
    workbook = openpyxl.load_workbook(
        io.BytesIO(asyncio.run(_response_bytes(export_response))),
        read_only=True,
    )
    assert workbook["分析摘要"]["B4"].value == "整楼"
    assert workbook["时间序列"]["C2"].value == "整楼"


def test_empty_live_detail_source_keeps_business_metrics_unavailable() -> None:
    with SessionLocal() as db:
        building = _activate_detail_pilot(db, with_business_data=False)
        snapshot = build_operating_snapshot(
            db,
            scope=_Scope(building_ids=(int(building.id),)),
            include_trend=False,
        )

    unavailable_codes = {
        "area.rentable_sqm",
        "area.leased_sqm",
        "occupancy.area_rate",
        "vacancy.area_sqm",
        "billing.receivable_month",
        "collection.received_month",
        "collection.rate_month",
        "arrears.balance",
        "rent.effective_daily",
        "expiry.contract_count_90d",
        "expiry.area_pressure_90d",
        "risk.open_items",
    }
    assert set(snapshot["metrics"]) == unavailable_codes
    for metric in snapshot["metrics"].values():
        assert metric["value"] is None
        assert metric["unavailable_reason"]
        assert metric["coverage"]["included_buildings"] == 0
        assert metric["coverage"]["source_ids"] == []
    assert snapshot["presentation"]["tower"] == []
    assert snapshot["detail_source_state"] == {
        "dashboard_facts_available": False,
        "dashboard_facts_unavailable_reason": "当前楼宇尚无可核验的房源面积明细",
    }


def test_zero_area_placeholder_does_not_turn_unknown_area_into_confirmed_zero() -> None:
    with SessionLocal() as db:
        building = _activate_detail_pilot(db, with_business_data=False)
        zone = db.query(Zone).filter(Zone.building_id == building.id).one()
        db.add(
            Room(
                building_id=building.id,
                zone_id=zone.id,
                tower="整楼",
                floor="",
                room_no="XSH-AREA-PENDING",
                room_type="办公",
                area=0,
                is_leasable=True,
            )
        )
        db.commit()
        snapshot = build_operating_snapshot(
            db,
            scope=_Scope(building_ids=(int(building.id),)),
            include_trend=False,
        )

    for code in (
        "area.rentable_sqm",
        "area.leased_sqm",
        "occupancy.area_rate",
        "vacancy.area_sqm",
    ):
        metric = snapshot["metrics"][code]
        assert metric["value"] is None
        assert "面积" in metric["unavailable_reason"]
        assert metric["coverage"]["included_buildings"] == 0
        assert metric["coverage"]["source_ids"] == []
    assert snapshot["detail_source_state"]["dashboard_facts_available"] is False


def test_operating_snapshot_rejects_non_live_adapter_without_demo_a_wording() -> None:
    with SessionLocal() as db:
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        with pytest.raises(HTTPException) as exc_info:
            build_operating_snapshot(
                db,
                scope=_Scope(building_ids=(int(building.id),)),
                include_trend=False,
            )

    assert exc_info.value.status_code == 422
    assert "明细" in str(exc_info.value.detail)
    assert "DEMO_A" not in str(exc_info.value.detail)
