from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

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


API_ROOT = "/api/snapshot-exchange/v1/uploads"
SAMPLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "snapshot-exchange"
    / "ops-exchange-v1-simulated.sample.json"
)

PACKAGE_FIELDS = (
    "exchange_version",
    "delivery_id",
    "source_system_code",
    "building_code",
    "metric_definition_version",
    "period_start",
    "period_end",
    "as_of_date",
    "source_observed_at",
    "source_updated_at",
    "source_published_at",
    "source_publication_revision",
    "source_publication_ref",
    "correction_of_delivery_id",
    "correction_reason",
    "data_classification",
    "digest_profile",
    "digest_algorithm",
    "content_digest",
)
METRIC_FIELDS = (
    "code",
    "value",
    "numerator",
    "denominator",
    "covered_area_sqm",
    "eligible_area_sqm",
    "covered_days",
    "eligible_days",
    "estimate_share",
    "estimate_method",
    "completeness_status",
    "freshness_status",
    "calculation_version",
    "source_batch_ref",
    "source_record_refs",
)


def _bootstrap(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register",
        json={
            "username": "bootstrap_admin",
            "password": "Admin123",
            "display_name": "初始化管理员",
        },
    )
    assert response.status_code == 200, response.text


def _uploader(
    username: str = "file_uploader",
    *,
    capability: str = "data_import_submit",
    building_code: str = "DEMO_B",
    role: str = "editor",
) -> dict[str, str]:
    with SessionLocal() as db:
        user = User(
            username=username,
            display_name=username,
            password_hash="unused-in-token-test",
            role=role,
            is_active=True,
            session_version=0,
        )
        db.add(user)
        db.flush()
        if capability:
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
        building = db.query(Building).filter(Building.code == building_code).one()
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


def _bind_source() -> None:
    with SessionLocal() as db:
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        source = DataSource(
            building_id=building.id,
            source_type="external_api",
            name="示例楼宇来源系统（上传测试）",
            owner="上传测试",
            cadence="monthly",
            freshness_rule="统计日后一个工作日内发布",
            is_active=True,
        )
        db.add(source)
        db.flush()
        db.add(
            SnapshotExchangeSourceBinding(
                source_id=source.id,
                source_system_code="DEMO-B-OPS",
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
                OperationLog,
            )
        )


def _xlsx_from_sample() -> bytes:
    sample = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    workbook = Workbook()
    package_sheet = workbook.active
    package_sheet.title = "Package"
    package_sheet.append(["field", "value"])
    package_sheet.append(["template_version", "OPS_EXCHANGE_XLSX_V1"])
    for field in PACKAGE_FIELDS:
        package_sheet.append([field, sample[field]])

    metrics_sheet = workbook.create_sheet("Metrics")
    metrics_sheet.append(list(METRIC_FIELDS))
    for metric in sample["metrics"]:
        row = []
        for field in METRIC_FIELDS:
            value = metric[field]
            if field == "source_record_refs":
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            row.append(value)
        metrics_sheet.append(row)

    workflow_sheet = workbook.create_sheet("Workflow")
    workflow_sheet.append(["stage", "actor_ref", "occurred_at"])
    for stage in ("submitted", "reviewed", "published"):
        workflow_sheet.append(
            [
                stage,
                sample["workflow"][stage]["actor_ref"],
                sample["workflow"][stage]["occurred_at"],
            ]
        )
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _mutate_xlsx(mutator) -> bytes:
    workbook = load_workbook(BytesIO(_xlsx_from_sample()))
    mutator(workbook)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _inject_excel_part(name: str, content: bytes = b"not-executable-test-content") -> bytes:
    output = BytesIO(_xlsx_from_sample())
    with ZipFile(output, mode="a", compression=ZIP_DEFLATED) as archive:
        archive.writestr(name, content)
    return output.getvalue()


def test_json_preflight_uses_content_not_filename_and_leaves_no_business_records() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _uploader()
        _bind_source()
        before = _business_counts()

        response = client.post(
            f"{API_ROOT}/preflight",
            headers=headers,
            files={
                "file": (
                    "伪装成表格.xlsx",
                    SAMPLE_PATH.read_bytes(),
                    "application/octet-stream",
                )
            },
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload == {
            "preflight_version": "OPS_EXCHANGE_PREFLIGHT_V1",
            "file_format": "json",
            "exchange_version": "OPS_EXCHANGE_V1",
            "delivery_id": "DELIVERY-20260805-SAMPLE-001",
            "source_system_code": "DEMO-B-OPS",
            "building_code": "DEMO_B",
            "server_content_digest": (
                "f0c33c3f4e58a833155aaa369258a13c542b093ddb7ac2baf1256e6397456917"
            ),
            "valid": True,
            "would_status": "accepted",
            "errors": [],
        }
        listed = client.get(f"{API_ROOT}/receipts", headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.json() == {"items": [], "total": 0}
        assert _business_counts() == before


def test_json_submit_is_idempotent_and_uploader_can_list_only_their_receipt() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _uploader()
        other_headers = _uploader(username="other_uploader")
        _bind_source()
        upload = {
            "file": (
                "任意名称.bin",
                SAMPLE_PATH.read_bytes(),
                "text/plain",
            )
        }

        first = client.post(f"{API_ROOT}/submit", headers=headers, files=upload)
        assert first.status_code == 201, first.text
        receipt = first.json()
        assert receipt["status"] == "accepted"
        assert receipt["center_status"] == "pending_review"
        assert first.headers["cache-control"] == "private, no-store"

        replay = client.post(f"{API_ROOT}/submit", headers=headers, files=upload)
        assert replay.status_code == 200, replay.text
        assert replay.json() == receipt

        listed = client.get(f"{API_ROOT}/receipts", headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.json() == {"items": [receipt], "total": 1}

        fetched = client.get(
            f"{API_ROOT}/receipts/{receipt['receipt_no']}",
            headers=headers,
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json() == receipt

        denied = client.get(
            f"{API_ROOT}/receipts/{receipt['receipt_no']}",
            headers=other_headers,
        )
        assert denied.status_code == 404, denied.text

        with SessionLocal() as db:
            assert db.query(SnapshotExchangeReceipt).count() == 1
            assert db.query(ImportBatch).count() == 1
            assert db.query(BuildingOperatingSnapshot).count() == 1
            assert db.query(SourceRecord).count() == 15
            assert db.query(BuildingMetricFact).count() == 15


def test_upload_requires_data_submit_capability_and_matching_editable_building_scope() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        authorized = _uploader(username="authorized")
        wrong_building = _uploader(username="wrong_building", building_code="DEMO_A")
        receiver_only = _uploader(
            username="receiver_only",
            capability="snapshot_exchange_receive",
        )
        viewer_with_capability = _uploader(
            username="viewer_with_capability",
            role="viewer",
        )
        _bind_source()

        sources = client.get(f"{API_ROOT}/sources", headers=authorized)
        assert sources.status_code == 200, sources.text
        assert sources.json() == [
            {
                "source_system_code": "DEMO-B-OPS",
                "building_code": "DEMO_B",
                "building_name": "示例主裙楼中心",
            }
        ]

        hidden_sources = client.get(f"{API_ROOT}/sources", headers=wrong_building)
        assert hidden_sources.status_code == 200, hidden_sources.text
        assert hidden_sources.json() == []

        denied_capability = client.post(
            f"{API_ROOT}/preflight",
            headers=receiver_only,
            files={"file": ("package.json", SAMPLE_PATH.read_bytes(), "application/json")},
        )
        assert denied_capability.status_code == 403, denied_capability.text

        denied_role = client.post(
            f"{API_ROOT}/preflight",
            headers=viewer_with_capability,
            files={"file": ("package.json", SAMPLE_PATH.read_bytes(), "application/json")},
        )
        assert denied_role.status_code == 403, denied_role.text

        denied_scope = client.post(
            f"{API_ROOT}/submit",
            headers=wrong_building,
            files={"file": ("package.json", SAMPLE_PATH.read_bytes(), "application/json")},
        )
        assert denied_scope.status_code == 404, denied_scope.text
        with SessionLocal() as db:
            assert db.query(SnapshotExchangeReceipt).count() == 0


def test_excel_preflight_maps_to_the_same_exchange_package_and_digest_as_json() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _uploader()
        _bind_source()

        response = client.post(
            f"{API_ROOT}/preflight",
            headers=headers,
            files={
                "file": (
                    "内容其实是Excel.json",
                    _xlsx_from_sample(),
                    "application/json",
                )
            },
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["file_format"] == "excel"
        assert payload["valid"] is True
        assert payload["would_status"] == "accepted"
        assert payload["delivery_id"] == "DELIVERY-20260805-SAMPLE-001"
        assert payload["server_content_digest"] == (
            "f0c33c3f4e58a833155aaa369258a13c542b093ddb7ac2baf1256e6397456917"
        )
        assert _business_counts()[0:5] == (0, 0, 0, 0, 0)


def test_excel_submit_can_fill_a_blank_digest_and_still_uses_the_shared_receiver() -> None:
    def clear_digest(workbook) -> None:
        package = workbook["Package"]
        for row_no in range(2, package.max_row + 1):
            if package.cell(row_no, 1).value == "content_digest":
                package.cell(row_no, 2).value = None
                return
        raise AssertionError("content_digest row missing")

    with TestClient(app) as client:
        _bootstrap(client)
        headers = _uploader()
        _bind_source()

        response = client.post(
            f"{API_ROOT}/submit",
            headers=headers,
            files={"file": ("sample.xlsx", _mutate_xlsx(clear_digest), "application/vnd.ms-excel")},
        )

        assert response.status_code == 201, response.text
        receipt = response.json()
        assert receipt["status"] == "accepted"
        assert receipt["server_content_digest"] == (
            "f0c33c3f4e58a833155aaa369258a13c542b093ddb7ac2baf1256e6397456917"
        )
        with SessionLocal() as db:
            assert db.query(SnapshotExchangeReceipt).count() == 1
            assert db.query(ImportBatch).one().parser_version == (
                "OPS_EXCHANGE_V1/OPS_CANONICAL_V1"
            )
            assert db.query(SourceRecord).count() == 15


def test_excel_rejects_formulas_hidden_fields_unknown_sheets_and_numeric_decimal_cells() -> None:
    cases = {
        "formula": (
            "excel_formula_not_allowed",
            lambda workbook: setattr(workbook["Metrics"]["B2"], "value", "=1+1"),
        ),
        "hidden_row": (
            "excel_hidden_row_not_allowed",
            lambda workbook: setattr(workbook["Metrics"].row_dimensions[2], "hidden", True),
        ),
        "unknown_sheet": (
            "excel_sheet_set_invalid",
            lambda workbook: workbook.create_sheet("AlternativeFields"),
        ),
        "numeric_decimal": (
            "excel_cell_type_invalid",
            lambda workbook: setattr(workbook["Metrics"]["B15"], "value", 0),
        ),
    }
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _uploader()
        _bind_source()

        for name, (expected_code, mutator) in cases.items():
            response = client.post(
                f"{API_ROOT}/preflight",
                headers=headers,
                files={"file": (f"{name}.xlsx", _mutate_xlsx(mutator), "application/json")},
            )
            assert response.status_code == 422, (name, response.text)
            payload = response.json()
            assert payload["errors"][0]["code"] == expected_code
            assert set(payload["errors"][0]) == {"code", "field", "row", "message"}
            serialized = json.dumps(payload, ensure_ascii=False)
            assert "lease-manager-phase1" not in serialized
            assert "Traceback" not in serialized

        with SessionLocal() as db:
            assert db.query(SnapshotExchangeReceipt).count() == 0
            assert db.query(ImportBatch).count() == 0
            assert db.query(BuildingOperatingSnapshot).count() == 0


def test_authorized_uploader_can_download_a_versioned_excel_template_without_internal_ids() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _uploader()
        _bind_source()

        response = client.get(
            f"{API_ROOT}/template.xlsx?source_system_code=DEMO-B-OPS",
            headers=headers,
        )

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert "OPS_EXCHANGE_XLSX_V1-template.xlsx" in response.headers["content-disposition"]
        workbook = load_workbook(BytesIO(response.content), data_only=False)
        assert workbook.sheetnames == ["Package", "Metrics", "Workflow"]
        package = {
            workbook["Package"].cell(row_no, 1).value: workbook["Package"].cell(row_no, 2).value
            for row_no in range(2, workbook["Package"].max_row + 1)
        }
        assert package["template_version"] == "OPS_EXCHANGE_XLSX_V1"
        assert package["source_system_code"] == "DEMO-B-OPS"
        assert package["building_code"] == "DEMO_B"
        assert package["content_digest"] is None
        assert "source_id" not in package
        assert "building_id" not in package
        assert [workbook["Metrics"].cell(row_no, 1).value for row_no in range(2, 17)] == sorted(
            [item["code"] for item in json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))["metrics"]]
        )
        assert not any(
            cell.data_type == "f"
            for sheet in workbook.worksheets
            for row in sheet.iter_rows()
            for cell in row
        )
        workbook.close()


def test_excel_macro_or_embedded_active_content_is_rejected_before_workbook_parsing() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _uploader()
        _bind_source()

        for part_name in (
            "xl/vbaProject.bin",
            "xl/activeX/activeX1.xml",
            "xl/embeddings/oleObject1.bin",
            "xl/externalLinks/externalLink1.xml",
        ):
            response = client.post(
                f"{API_ROOT}/preflight",
                headers=headers,
                files={"file": ("safe-looking.xlsx", _inject_excel_part(part_name), "application/json")},
            )
            assert response.status_code == 422, (part_name, response.text)
            assert response.json()["errors"] == [
                {
                    "code": "excel_active_content_not_allowed",
                    "field": None,
                    "row": None,
                    "message": "Excel 文件包含宏、外部链接或嵌入对象",
                }
            ]

        with SessionLocal() as db:
            assert db.query(SnapshotExchangeReceipt).count() == 0
