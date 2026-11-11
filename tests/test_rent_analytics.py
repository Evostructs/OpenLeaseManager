from __future__ import annotations

import calendar
from datetime import date, timedelta
import io

from fastapi.testclient import TestClient
import openpyxl
from dateutil.relativedelta import relativedelta

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import Building, Contract, ContractRoom, OperationLog, RentTerm, Room, Zone
from app.services.contract_rent import calculate_monthly_rent


def _client() -> TestClient:
    return TestClient(app)


def _register_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
    )
    assert response.status_code == 200
    _login(client, "admin", "Admin123")


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _demo_a_scope(db, tower: str) -> tuple[int, int]:
    building_id = db.query(Building.id).filter(Building.code == "DEMO_A").scalar()
    assert building_id is not None
    zone_code = {
        "南塔": "SOUTH_OFFICE",
        "北塔": "NORTH_OFFICE",
        "商业配套": "COMMERCIAL_SUPPORT",
    }.get(tower, "SOUTH_OFFICE")
    zone_id = (
        db.query(Zone.id)
        .filter(Zone.building_id == building_id, Zone.code == zone_code)
        .scalar()
    )
    assert zone_id is not None
    return int(building_id), int(zone_id)


def _add_contract(
    contract_no: str,
    *,
    tower: str = "南塔",
    room_no: str | None = None,
    area: float = 100,
    unit_price: float = 4,
    start_date: date,
    end_date: date,
    is_deleted: bool = False,
    with_room: bool = True,
    free_period: int = 0,
) -> int:
    monthly_rent = calculate_monthly_rent(area, unit_price)
    db = SessionLocal()
    try:
        building_id, zone_id = _demo_a_scope(db, tower)
        resolved_room_no = room_no or f"{tower[:1]}-{contract_no}"
        room = None
        if with_room:
            room = Room(
                building_id=building_id,
                zone_id=zone_id,
                tower=tower,
                floor="9",
                room_no=resolved_room_no,
                room_type="办公",
                area=area,
                is_leasable=True,
                is_deleted=False,
            )
            db.add(room)
            db.flush()
        contract = Contract(
            building_id=building_id,
            contract_no=contract_no,
            tower=tower,
            room_no=resolved_room_no,
            tenant_name=f"客户-{contract_no}",
            area=area,
            unit_price=unit_price,
            monthly_rent=monthly_rent,
            free_period=free_period,
            start_date=start_date,
            end_date=end_date,
            is_deleted=is_deleted,
        )
        db.add(contract)
        db.flush()
        if with_room:
            db.add(
                ContractRoom(
                    building_id=building_id,
                    contract_id=contract.id,
                    room_id=room.id,
                    tower=tower,
                    room_no=resolved_room_no,
                    area=area,
                    unit_price=unit_price,
                    monthly_rent=monthly_rent,
                    start_date=start_date,
                    end_date=end_date,
                )
            )
        db.commit()
        return contract.id
    finally:
        db.close()


def _daily_row(payload: dict, target: date) -> dict:
    return next(item for item in payload["daily"] if item["date"] == target.isoformat())


def test_rent_analytics_returns_area_weighted_daily_segments_and_phases() -> None:
    today = today_in_timezone()
    start = today - timedelta(days=10)
    end = today + timedelta(days=10)
    _add_contract("RENT-SOUTH", tower="南塔", area=100, unit_price=4, start_date=start, end_date=end)
    _add_contract("RENT-NORTH", tower="北塔", area=300, unit_price=6, start_date=start, end_date=end)

    with _client() as client:
        _register_admin(client)
        response = client.get("/api/rent-analytics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["contract_version"] == "rent-analytics-v1"
    assert payload["coverage"]["total_contracts"] == 2
    assert payload["coverage"]["included_contracts"] == 2
    assert payload["coverage"]["excluded_contracts"] == 0
    assert payload["bounds"]["default_end"] == today.isoformat()

    row = _daily_row(payload, today)
    month_days = calendar.monthrange(today.year, today.month)[1]
    expected_rent = (calculate_monthly_rent(100, 4) + calculate_monthly_rent(300, 6)) / month_days
    assert row["phase"] == "history"
    assert abs(row["segments"]["overall"]["rent"] - expected_rent) < 0.02
    assert row["segments"]["overall"]["area"] == 400
    assert abs(row["segments"]["overall"]["effective_daily_rent"] - expected_rent / 400) < 0.0001
    assert row["segments"]["south_office"]["area"] == 100
    assert row["segments"]["north_office"]["area"] == 300
    assert _daily_row(payload, today + timedelta(days=1))["phase"] == "forecast"
    assert {item["key"] for item in payload["segments"]} == {
        "overall",
        "south_office",
        "north_office",
        "commercial",
    }


def test_default_bounds_cover_exact_trailing_and_future_year_plus_final_exit() -> None:
    today = today_in_timezone()
    contract_start = today - timedelta(days=2)
    contract_end = today + timedelta(days=10)
    contract_id = _add_contract(
        "RENT-BOUNDS",
        start_date=contract_start,
        end_date=contract_end,
    )

    with _client() as client:
        _register_admin(client)
        payload = client.get("/api/rent-analytics").json()

    trailing_start = today + relativedelta(years=-1) + timedelta(days=1)
    future_end = today + relativedelta(years=1)
    exit_date = contract_end + timedelta(days=1)
    assert payload["bounds"]["min_date"] == trailing_start.isoformat()
    assert payload["bounds"]["max_date"] == future_end.isoformat()
    assert payload["bounds"]["default_start"] == trailing_start.isoformat()
    assert payload["bounds"]["default_end"] == today.isoformat()
    assert payload["daily"][0]["date"] == trailing_start.isoformat()
    assert payload["daily"][-1]["date"] == future_end.isoformat()
    assert _daily_row(payload, exit_date)["segments"]["overall"]["area"] == 0
    assert any(
        event["contract_id"] == contract_id
        and "contract_end" in event["type"].split("+")
        and event["date"] == exit_date.isoformat()
        for event in payload["events"]
    )


def test_default_bounds_accept_thirty_year_contract_history() -> None:
    today = today_in_timezone()
    contract_start = today + relativedelta(years=-30)
    _add_contract(
        "RENT-THIRTY-YEARS",
        start_date=contract_start,
        end_date=today,
    )

    with _client() as client:
        _register_admin(client)
        response = client.get("/api/rent-analytics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["bounds"]["requested_start"] == contract_start.isoformat()
    assert payload["daily"][0]["date"] == contract_start.isoformat()


def test_zero_rent_requires_an_explicit_free_or_zero_basis() -> None:
    today = today_in_timezone()
    end = today + timedelta(days=10)
    _add_contract(
        "RENT-ZERO-UNKNOWN",
        area=100,
        unit_price=0,
        start_date=today,
        end_date=end,
    )
    _add_contract(
        "RENT-ZERO-FREE",
        area=100,
        unit_price=0,
        start_date=today,
        end_date=end,
        free_period=1,
    )
    explicit_zero_id = _add_contract(
        "RENT-ZERO-TERM",
        area=100,
        unit_price=0,
        start_date=today,
        end_date=end,
    )
    db = SessionLocal()
    try:
        db.add(
            RentTerm(
                contract_id=explicit_zero_id,
                rule_type="fixed_monthly",
                start_date=today,
                end_date=end,
                monthly_amount=0,
            )
        )
        db.commit()
    finally:
        db.close()

    with _client() as client:
        _register_admin(client)
        payload = client.get(
            "/api/rent-analytics",
            params={"start_date": today.isoformat(), "end_date": end.isoformat()},
        ).json()

    assert payload["coverage"]["total_contracts"] == 3
    assert payload["coverage"]["included_contracts"] == 2
    assert payload["coverage"]["excluded_contracts"] == 1
    assert payload["coverage"]["excluded"][0]["contract_no"] == "RENT-ZERO-UNKNOWN"
    today_metric = _daily_row(payload, today)["segments"]["overall"]
    assert today_metric["area"] == 200
    assert today_metric["rent"] == 0
    assert today_metric["effective_daily_rent"] == 0


def test_rent_analytics_applies_rent_free_terms_without_dropping_area() -> None:
    today = today_in_timezone()
    start = today - timedelta(days=5)
    end = today + timedelta(days=5)
    contract_id = _add_contract(
        "RENT-FREE",
        tower="南塔",
        area=100,
        unit_price=5,
        start_date=start,
        end_date=end,
    )
    db = SessionLocal()
    try:
        room_item = db.query(ContractRoom).filter(ContractRoom.contract_id == contract_id).one()
        db.add_all(
            [
                RentTerm(
                    contract_id=contract_id,
                    contract_room_id=room_item.id,
                    room_no=room_item.room_no,
                    rule_type="area_unit_price",
                    start_date=start,
                    end_date=end,
                    area=100,
                    unit_price=5,
                ),
                RentTerm(
                    contract_id=contract_id,
                    contract_room_id=room_item.id,
                    room_no=room_item.room_no,
                    rule_type="rent_free",
                    start_date=today,
                    end_date=today,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    with _client() as client:
        _register_admin(client)
        payload = client.get("/api/rent-analytics").json()

    free_row = _daily_row(payload, today)["segments"]["overall"]
    billed_row = _daily_row(payload, today + timedelta(days=1))["segments"]["overall"]
    assert free_row["area"] == 100
    assert free_row["rent"] == 0
    assert free_row["effective_daily_rent"] == 0
    assert billed_row["rent"] > 0
    assert any(
        event["contract_id"] == contract_id
        and event["date"] == (today + timedelta(days=1)).isoformat()
        and "免租" in event["label"]
        for event in payload["events"]
    )


def test_rent_analytics_keeps_expired_history_and_reports_exclusions() -> None:
    today = today_in_timezone()
    historical_start = today - timedelta(days=30)
    historical_end = today - timedelta(days=20)
    _add_contract(
        "RENT-HISTORY",
        area=120,
        unit_price=4.5,
        start_date=historical_start,
        end_date=historical_end,
    )
    _add_contract(
        "RENT-INVALID",
        area=0,
        unit_price=0,
        start_date=historical_start,
        end_date=historical_end,
        with_room=False,
    )
    _add_contract(
        "RENT-DELETED",
        area=100,
        unit_price=4,
        start_date=historical_start,
        end_date=historical_end,
        is_deleted=True,
    )

    with _client() as client:
        _register_admin(client)
        payload = client.get("/api/rent-analytics").json()

    assert payload["coverage"]["total_contracts"] == 2
    assert payload["coverage"]["included_contracts"] == 1
    assert payload["coverage"]["excluded_contracts"] == 1
    assert payload["coverage"]["coverage_rate"] == 50
    assert payload["coverage"]["contract_day_coverage_rate"] == 50
    assert payload["coverage"]["excluded"][0]["contract_no"] == "RENT-INVALID"
    assert payload["coverage"]["excluded"][0]["start_date"] == historical_start.isoformat()
    assert payload["coverage"]["excluded"][0]["end_date"] == historical_end.isoformat()
    assert _daily_row(payload, historical_start)["segments"]["overall"]["effective_daily_rent"] > 0
    assert _daily_row(payload, today)["segments"]["overall"]["effective_daily_rent"] is None


def test_historical_segment_uses_soft_deleted_room_metadata_by_room_id() -> None:
    today = today_in_timezone()
    start = today - timedelta(days=30)
    end = today - timedelta(days=20)
    db = SessionLocal()
    try:
        building_id, _south_office_zone_id = _demo_a_scope(db, "南塔")
        _, commercial_zone_id = _demo_a_scope(db, "商业配套")
        deleted_commercial_room = Room(
            building_id=building_id,
            zone_id=commercial_zone_id,
            tower="南塔",
            floor="1",
            room_no="RENT-HISTORY-DELETED",
            room_type="办公",
            area=100,
            is_leasable=True,
            is_deleted=True,
        )
        db.add(deleted_commercial_room)
        db.flush()
        monthly_rent = calculate_monthly_rent(100, 4)
        contract = Contract(
            building_id=building_id,
            contract_no="RENT-HISTORY-ROOM-META",
            tower="南塔",
            room_no=deleted_commercial_room.room_no,
            tenant_name="历史房源客户",
            area=100,
            unit_price=4,
            monthly_rent=monthly_rent,
            start_date=start,
            end_date=end,
            is_deleted=False,
        )
        db.add(contract)
        db.flush()
        db.add(
            ContractRoom(
                building_id=building_id,
                contract_id=contract.id,
                room_id=deleted_commercial_room.id,
                tower="南塔",
                room_no=deleted_commercial_room.room_no,
                area=100,
                unit_price=4,
                monthly_rent=monthly_rent,
                start_date=start,
                end_date=end,
            )
        )
        db.commit()
    finally:
        db.close()

    with _client() as client:
        _register_admin(client)
        payload = client.get(
            "/api/rent-analytics",
            params={"start_date": start.isoformat(), "end_date": end.isoformat()},
        ).json()

    segments = _daily_row(payload, start)["segments"]
    assert segments["commercial"]["area"] == 100
    assert segments["south_office"]["area"] == 0


def test_rent_analytics_excel_requires_editor_and_records_audit() -> None:
    today = today_in_timezone()
    start = today - timedelta(days=20)
    _add_contract("RENT-EXPORT", start_date=start, end_date=today)
    _add_contract(
        "RENT-EXPORT-NORTH",
        tower="北塔",
        start_date=start,
        end_date=today,
    )

    with _client() as admin:
        _register_admin(admin)
        assert admin.post(
            "/api/auth/create-user",
            json={"username": "viewer", "password": "Viewer123", "display_name": "Viewer", "role": "viewer"},
        ).status_code == 200
        assert admin.post(
            "/api/auth/create-user",
            json={"username": "editor", "password": "Editor123", "display_name": "Editor", "role": "editor"},
        ).status_code == 200

        with _client() as viewer:
            _login(viewer, "viewer", "Viewer123")
            assert viewer.get("/api/rent-analytics").status_code == 200
            denied = viewer.get(
                "/api/rent-analytics/export",
                params={"start_date": start.isoformat(), "end_date": today.isoformat()},
            )
            assert denied.status_code == 403

        with _client() as editor:
            _login(editor, "editor", "Editor123")
            exported = editor.get(
                "/api/rent-analytics/export",
                params={
                    "start_date": start.isoformat(),
                    "end_date": today.isoformat(),
                    "segments": "south_office",
                },
            )
            assert exported.status_code == 200
            assert "spreadsheetml" in exported.headers["content-type"]
            workbook = openpyxl.load_workbook(io.BytesIO(exported.content), data_only=True)
            assert workbook.sheetnames == ["分析摘要", "时间序列", "变化驱动", "排除记录", "口径说明"]
            assert workbook["分析摘要"]["A1"].value == "租金时间分析"
            assert workbook["时间序列"].max_row > 1
            driver_sheet = workbook["变化驱动"]
            assert [cell.value for cell in driver_sheet[1]][2] == "分区"
            assert driver_sheet["C2"].value == "南塔办公"
            assert driver_sheet["H2"].value != 0 or driver_sheet["I2"].value != 0
            assert {
                cell.value for cell in driver_sheet["E"][1:]
            } == {"RENT-EXPORT"}

        logs = admin.get("/api/audit/logs", params={"action": "rent_analytics_export"}).json()
        assert logs["total"] == 1
        assert logs["items"][0]["operator"] == "editor"

    db = SessionLocal()
    try:
        row = db.query(OperationLog).filter(OperationLog.action == "rent_analytics_export").one()
        assert start.isoformat() in (row.changes or "")
        assert today.isoformat() in (row.changes or "")
    finally:
        db.close()


def test_rent_analytics_summary_splits_history_and_forecast_without_renewal_assumption() -> None:
    today = today_in_timezone()
    start = today - timedelta(days=2)
    signed_end = today + timedelta(days=1)
    end = today + timedelta(days=3)
    _add_contract("RENT-MIXED", start_date=start, end_date=signed_end)

    with _client() as client:
        _register_admin(client)
        response = client.get(
            "/api/rent-analytics",
            params={"start_date": start.isoformat(), "end_date": end.isoformat()},
        )

    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["phase"] == "mixed"
    assert summary["split_history_forecast"] is True
    assert summary["segments"]["overall"]["history"]["calendar_days"] == 3
    assert summary["segments"]["overall"]["forecast"]["calendar_days"] == 3
    assert summary["segments"]["overall"]["forecast"]["active_days"] == 1
    assert _daily_row(payload, signed_end)["segments"]["overall"]["effective_daily_rent"] is not None
    assert _daily_row(payload, signed_end + timedelta(days=1))["segments"]["overall"]["effective_daily_rent"] is None


def test_contract_level_rent_reconciles_across_multi_tower_area_allocation() -> None:
    today = today_in_timezone()
    start = today - timedelta(days=2)
    end = today + timedelta(days=2)
    db = SessionLocal()
    try:
        building_id, south_zone_id = _demo_a_scope(db, "南塔")
        _, north_zone_id = _demo_a_scope(db, "北塔")
        south = Room(building_id=building_id, zone_id=south_zone_id, tower="南塔", floor="9", room_no="RENT-MULTI-S", room_type="办公", area=100, is_leasable=True, is_deleted=False)
        north = Room(building_id=building_id, zone_id=north_zone_id, tower="北塔", floor="9", room_no="RENT-MULTI-N", room_type="办公", area=300, is_leasable=True, is_deleted=False)
        db.add_all([south, north])
        db.flush()
        contract = Contract(
            building_id=building_id,
            contract_no="RENT-MULTI",
            tower="多房源",
            room_no="RENT-MULTI-S、RENT-MULTI-N",
            tenant_name="跨分区客户",
            area=400,
            unit_price=0,
            monthly_rent=31000,
            start_date=start,
            end_date=end,
            is_deleted=False,
        )
        db.add(contract)
        db.flush()
        db.add_all(
            [
                ContractRoom(
                    building_id=building_id,
                    contract_id=contract.id,
                    room_id=south.id,
                    tower="南塔",
                    room_no=south.room_no,
                    area=100,
                    unit_price=0,
                    monthly_rent=0,
                    start_date=start,
                    end_date=end,
                ),
                ContractRoom(
                    building_id=building_id,
                    contract_id=contract.id,
                    room_id=north.id,
                    tower="北塔",
                    room_no=north.room_no,
                    area=300,
                    unit_price=0,
                    monthly_rent=0,
                    start_date=start,
                    end_date=end,
                ),
                RentTerm(
                    contract_id=contract.id,
                    rule_type="fixed_monthly",
                    start_date=start,
                    end_date=end,
                    monthly_amount=31000,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    with _client() as client:
        _register_admin(client)
        payload = client.get(
            "/api/rent-analytics",
            params={"start_date": today.isoformat(), "end_date": today.isoformat()},
        ).json()

    segments = _daily_row(payload, today)["segments"]
    assert segments["overall"]["area"] == 400
    assert segments["south_office"]["area"] == 100
    assert segments["north_office"]["area"] == 300
    assert abs(
        segments["overall"]["rent"]
        - segments["south_office"]["rent"]
        - segments["north_office"]["rent"]
    ) < 0.0001
    assert abs(segments["north_office"]["rent"] / segments["south_office"]["rent"] - 3) < 0.0001


def test_rent_analytics_supports_exact_ranges_comparisons_and_validates_inputs() -> None:
    today = today_in_timezone()
    contract_start = today - timedelta(days=400)
    _add_contract("RENT-COMPARE", start_date=contract_start, end_date=today + timedelta(days=10))
    start = today - timedelta(days=29)

    with _client() as client:
        _register_admin(client)
        response = client.get(
            "/api/rent-analytics",
            params={"start_date": start.isoformat(), "end_date": today.isoformat()},
        )
        bad_order = client.get(
            "/api/rent-analytics",
            params={"start_date": today.isoformat(), "end_date": start.isoformat()},
        )
        bad_segment = client.get("/api/rent-analytics", params={"segments": "overall,unknown"})
        too_long = client.get(
            "/api/rent-analytics",
            params={
                "start_date": (today - timedelta(days=50 * 366 + 1)).isoformat(),
                "end_date": today.isoformat(),
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["daily"]) == 30
    assert payload["bounds"]["requested_start"] == start.isoformat()
    assert payload["bounds"]["requested_end"] == today.isoformat()
    assert payload["comparisons"]["previous_period"]["available"] is True
    assert payload["comparisons"]["year_over_year"]["available"] is True
    assert payload["summary"]["segments"]["overall"]["combined"]["area_days"] == 3000
    assert bad_order.status_code == 422
    assert bad_segment.status_code == 422
    assert too_long.status_code == 422
