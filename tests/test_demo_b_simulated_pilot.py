from __future__ import annotations

from pathlib import Path
import pytest

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.core.security import create_access_token
from app.core.time import now_in_timezone, today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import (
    Building,
    BuildingMetricFact,
    BuildingOperatingSnapshot,
    DataSource,
    ImportBatch,
    OperationLog,
    SnapshotExchangeReceipt,
    SnapshotExchangeSourceBinding,
    SourceRecord,
    User,
    UserCapability,
    UserScopeGrant,
)
from app.services.snapshot_exchange_uploads import parse_upload


ROOT = Path(__file__).resolve().parents[1]
PILOT_DIR = ROOT / "docs" / "pilot-onboarding"
pytestmark = pytest.mark.skipif(not PILOT_DIR.exists(), reason="Internal pilot fixtures are not distributed in the public edition.")
JSON_PACKAGE = PILOT_DIR / "demo-b-ops-exchange-simulated-v1.json"
EXCEL_PACKAGE = PILOT_DIR / "demo-b-ops-exchange-simulated-v1.xlsx"
INTAKE_WORKBOOK = PILOT_DIR / "pilot-source-intake-demo-b-simulated-v1.xlsx"
API_ROOT = "/api/snapshot-exchange/v1/uploads"
EXPECTED_DIGEST = "8b21232ceb0371be46f64c24a02216bf49a3c4e234ffb1cbf526292bd13bcf8e"


def _bootstrap(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register",
        json={
            "username": "sim_bootstrap_admin",
            "password": "Admin123",
            "display_name": "模拟试点初始化管理员",
        },
    )
    assert response.status_code == 200, response.text


def _actor(username: str, capability: str) -> dict[str, str]:
    with SessionLocal() as db:
        user = User(
            username=username,
            display_name=username,
            password_hash="unused-in-token-test",
            role="editor",
            is_active=True,
            session_version=0,
        )
        db.add(user)
        db.flush()
        db.add(
            UserCapability(
                user_id=user.id,
                capability=capability,
                valid_from=today_in_timezone(),
                valid_to=None,
                granted_by=None,
                granted_at=now_in_timezone(),
            )
        )
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        db.add(
            UserScopeGrant(
                user_id=user.id,
                scope_type="building",
                portfolio_id=None,
                building_id=building.id,
                access_level="edit",
                include_future_buildings=False,
                valid_from=today_in_timezone(),
                valid_to=None,
                granted_by=None,
                granted_at=now_in_timezone(),
            )
        )
        db.commit()
        token = create_access_token(
            user_id=int(user.id),
            username=user.username,
            role=user.role,
            session_version=int(user.session_version or 0),
        )
    return {"Authorization": f"Bearer {token}"}


def _bind_simulated_source() -> None:
    with SessionLocal() as db:
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        source = DataSource(
            building_id=building.id,
            source_type="external_api",
            name="示例主裙楼中心模拟经营台账",
            owner="模拟试点",
            cadence="monthly",
            freshness_rule="模拟月结后一个工作日内发布",
            is_active=True,
        )
        db.add(source)
        db.flush()
        db.add(
            SnapshotExchangeSourceBinding(
                source_id=source.id,
                source_system_code="DEMO-B-SIM-OPS",
                is_active=True,
            )
        )
        db.commit()


def _business_counts() -> tuple[int, ...]:
    with SessionLocal() as db:
        return tuple(
            db.query(model).count()
            for model in (
                SnapshotExchangeReceipt,
                ImportBatch,
                SourceRecord,
                BuildingMetricFact,
                BuildingOperatingSnapshot,
            )
        )


def test_demo_b_simulated_artifacts_are_complete_equivalent_and_explicitly_simulated() -> None:
    json_format, json_package = parse_upload(JSON_PACKAGE.read_bytes())
    excel_format, excel_package = parse_upload(EXCEL_PACKAGE.read_bytes())

    assert json_format == "json"
    assert excel_format == "excel"
    assert json_package.model_dump(mode="json") == excel_package.model_dump(mode="json")
    assert json_package.building_code == "DEMO_B"
    assert json_package.source_system_code == "DEMO-B-SIM-OPS"
    assert json_package.data_classification == "simulated"
    assert json_package.content_digest == EXPECTED_DIGEST
    assert len(json_package.metrics) == 15
    assert all(item.completeness_status == "complete" for item in json_package.metrics)
    assert all(item.freshness_status == "current" for item in json_package.metrics)
    assert all(item.estimate_share == "0" for item in json_package.metrics)

    intake = load_workbook(INTAKE_WORKBOOK, data_only=False, read_only=True)
    assert intake.worksheets[1]["C5"].value == "DEMO_B"
    assert intake.worksheets[1]["C9"].value == "DEMO-B-SIM-OPS"
    assert all(
        intake.worksheets[3].cell(row, 18).value == "已确认（仅模拟）"
        for row in range(5, 20)
    )
    assert all(
        intake.worksheets[7].cell(row, 3).value is not None
        for row in range(5, 17)
    )
    assert not any(
        isinstance(cell.value, str) and cell.value.startswith("=")
        for sheet in intake.worksheets
        for row in sheet.iter_rows()
        for cell in row
    )
    intake.close()


def test_demo_b_simulated_full_chain_preflight_submit_replay_review_publish() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        uploader = _actor("sim_center_uploader", "data_import_submit")
        reviewer = _actor("sim_center_reviewer", "snapshot_review")
        publisher = _actor("sim_center_publisher", "snapshot_publish")
        _bind_simulated_source()
        before = _business_counts()

        json_preflight = client.post(
            f"{API_ROOT}/preflight",
            headers=uploader,
            files={"file": ("simulated.json", JSON_PACKAGE.read_bytes(), "application/json")},
        )
        assert json_preflight.status_code == 200, json_preflight.text
        assert json_preflight.json()["file_format"] == "json"
        assert json_preflight.json()["valid"] is True
        assert json_preflight.json()["would_status"] == "accepted"
        assert json_preflight.json()["server_content_digest"] == EXPECTED_DIGEST
        assert _business_counts() == before

        excel_preflight = client.post(
            f"{API_ROOT}/preflight",
            headers=uploader,
            files={
                "file": (
                    "simulated.xlsx",
                    EXCEL_PACKAGE.read_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert excel_preflight.status_code == 200, excel_preflight.text
        assert excel_preflight.json()["file_format"] == "excel"
        assert excel_preflight.json()["server_content_digest"] == EXPECTED_DIGEST
        assert _business_counts() == before

        submitted = client.post(
            f"{API_ROOT}/submit",
            headers=uploader,
            files={"file": ("simulated.json", JSON_PACKAGE.read_bytes(), "application/json")},
        )
        assert submitted.status_code == 201, submitted.text
        receipt = submitted.json()
        assert receipt["status"] == "accepted"
        assert receipt["center_status"] == "pending_review"
        assert receipt["building_code"] == "DEMO_B"
        assert receipt["server_content_digest"] == EXPECTED_DIGEST

        replay = client.post(
            f"{API_ROOT}/submit",
            headers=uploader,
            files={
                "file": (
                    "same-business-content.xlsx",
                    EXCEL_PACKAGE.read_bytes(),
                    "application/octet-stream",
                )
            },
        )
        assert replay.status_code == 200, replay.text
        assert replay.json() == receipt

        receipts = client.get(f"{API_ROOT}/receipts", headers=uploader)
        assert receipts.status_code == 200, receipts.text
        assert receipts.json() == {"items": [receipt], "total": 1}

        with SessionLocal() as db:
            stored_receipt = db.query(SnapshotExchangeReceipt).one()
            snapshot_id = int(stored_receipt.candidate_snapshot_id)
            assert stored_receipt.content_digest == EXPECTED_DIGEST
            assert db.query(ImportBatch).count() == 1
            assert db.query(SourceRecord).count() == 15
            assert db.query(BuildingMetricFact).count() == 15
            assert db.query(BuildingOperatingSnapshot).count() == 1

        reviewed = client.post(
            f"/api/operations/snapshot-submissions/{snapshot_id}/review",
            headers=reviewer,
            params={"building": "DEMO_B"},
            json={"approved": True, "notes": "模拟来源、口径和责任链核对通过"},
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["publication_status"] == "reviewed"
        assert reviewed.json()["exchange_evidence"]["delivery_id"] == "DEMO-B-SIM-202607-R1"
        assert reviewed.json()["exchange_evidence"]["source_system_code"] == "DEMO-B-SIM-OPS"

        published = client.post(
            f"/api/operations/snapshot-submissions/{snapshot_id}/publish",
            headers=publisher,
            params={"building": "DEMO_B"},
            json={"notes": "发布示例主裙楼中心模拟试点快照"},
        )
        assert published.status_code == 200, published.text
        assert published.json()["publication_status"] == "published"

        result = client.get(
            "/api/operations/published-snapshot",
            headers=publisher,
            params={"building": "DEMO_B", "as_of_date": "2026-07-31"},
        )
        assert result.status_code == 200, result.text
        metrics = result.json()["metrics"]
        assert metrics["area.rentable_sqm"]["value"] == 88000
        assert metrics["area.leased_sqm"]["value"] == 83600
        assert metrics["vacancy.area_sqm"]["value"] == 4400
        assert metrics["occupancy.area_rate"]["value"] == 95
        assert metrics["collection.rate_month"]["value"] == 95
        assert metrics["expiry.rent_12m"]["value"] == 16800000
        assert metrics["risk.open_items"]["value"] == 3
        assert all(
            metrics[code]["coverage"]["included_buildings"] == 1
            for code in (
                "area.rentable_sqm",
                "area.leased_sqm",
                "occupancy.area_rate",
                "collection.rate_month",
                "expiry.rent_12m",
            )
        )

        with SessionLocal() as db:
            snapshot = db.get(BuildingOperatingSnapshot, snapshot_id)
            assert snapshot.publication_status == "published"
            assert snapshot.is_current is True
            assert db.query(ImportBatch).one().status == "published"
            assert {
                item.publication_status
                for item in db.query(BuildingMetricFact).filter_by(snapshot_id=snapshot_id).all()
            } == {"published"}
            actions = {
                item.action
                for item in db.query(OperationLog)
                .filter(OperationLog.target_id == snapshot_id)
                .all()
            }
            assert {"snapshot_submit", "snapshot_review", "snapshot_publish"} <= actions
