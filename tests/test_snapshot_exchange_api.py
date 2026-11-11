from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.core.security import create_access_token
from app.core.maintenance import maintenance_mode
from app.core.time import now_in_timezone, today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import (
    Building,
    DataSource,
    ImportBatch,
    Portfolio,
    SnapshotExchangeReceipt,
    SnapshotExchangeSourceBinding,
    User,
    UserCapability,
    UserPortfolioOverviewGrant,
    UserScopeGrant,
)
from app.ops_v1 import OPS_V1_BY_CODE, SUBMITTABLE_CODES
from app.services import snapshot_exchange as exchange_service
from app.snapshot_exchange_schemas import (
    SnapshotExchangePackageV1,
    compute_content_digest,
)


API_ROOT = "/api/snapshot-exchange/v1"


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


def _principal(
    username: str,
    *,
    role: str,
    capabilities: tuple[str, ...] = (),
    portfolio_overview: bool = False,
    building_scope: str | None = None,
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
        for capability in capabilities:
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
        if building_scope is not None:
            building = db.query(Building).filter(Building.code == building_scope).one()
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
        if portfolio_overview:
            portfolio = (
                db.query(Portfolio)
                .filter(Portfolio.code == "DEMO_OFFICE_PORTFOLIO")
                .one()
            )
            db.add(
                UserPortfolioOverviewGrant(
                    user_id=user.id,
                    portfolio_id=portfolio.id,
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
            name="示例楼宇来源系统（接口测试）",
            owner="接口测试",
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


def _package(*, delivery_id: str = "DELIVERY-20260805-API-001") -> SnapshotExchangePackageV1:
    metrics = []
    for index, code in enumerate(sorted(SUBMITTABLE_CODES), start=1):
        aggregation = OPS_V1_BY_CODE[code]["aggregation_rule"]
        if aggregation == "ratio_percent":
            numerator, denominator, value = "1", "2", "50"
        elif aggregation == "ratio":
            numerator, denominator, value = "1", "2", "0.5"
        else:
            numerator, denominator, value = "100", None, "100"
        metrics.append(
            {
                "code": code,
                "value": value,
                "numerator": numerator,
                "denominator": denominator,
                "covered_area_sqm": "100",
                "eligible_area_sqm": "100",
                "covered_days": 5,
                "eligible_days": 31,
                "estimate_share": "0",
                "estimate_method": "",
                "completeness_status": "partial",
                "freshness_status": "current",
                "calculation_version": "ops-source-v1",
                "source_batch_ref": "BATCH-20260805-API-001",
                "source_record_refs": [f"REC-{index:03d}"],
            }
        )
    raw = {
        "exchange_version": "OPS_EXCHANGE_V1",
        "delivery_id": delivery_id,
        "source_system_code": "DEMO-B-OPS",
        "building_code": "DEMO_B",
        "metric_definition_version": "OPS_V1",
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "as_of_date": "2026-08-05",
        "source_observed_at": "2026-08-05T08:00:00Z",
        "source_updated_at": "2026-08-05T07:30:00Z",
        "source_published_at": "2026-08-05T09:00:00Z",
        "source_publication_revision": 1,
        "source_publication_ref": "PUB-20260805-API-001",
        "correction_of_delivery_id": None,
        "correction_reason": None,
        "data_classification": "simulated",
        "digest_profile": "OPS_CANONICAL_V1",
        "digest_algorithm": "sha-256",
        "content_digest": "0" * 64,
        "metrics": metrics,
        "workflow": {
            "submitted": {
                "actor_ref": "user:submitter",
                "occurred_at": "2026-08-05T08:10:00Z",
            },
            "reviewed": {
                "actor_ref": "user:reviewer",
                "occurred_at": "2026-08-05T08:30:00Z",
            },
            "published": {
                "actor_ref": "user:publisher",
                "occurred_at": "2026-08-05T09:00:00Z",
            },
        },
    }
    unsigned = SnapshotExchangePackageV1.model_validate(raw)
    raw["content_digest"] = compute_content_digest(unsigned)
    return SnapshotExchangePackageV1.model_validate(raw)


def _changed(package: SnapshotExchangePackageV1) -> SnapshotExchangePackageV1:
    raw = package.model_dump(mode="json")
    raw["metrics"][0]["numerator"] = "101"
    raw["metrics"][0]["value"] = "101"
    raw["content_digest"] = "0" * 64
    unsigned = SnapshotExchangePackageV1.model_validate(raw)
    raw["content_digest"] = compute_content_digest(unsigned)
    return SnapshotExchangePackageV1.model_validate(raw)


def _resign(raw: dict) -> SnapshotExchangePackageV1:
    raw["content_digest"] = "0" * 64
    unsigned = SnapshotExchangePackageV1.model_validate(raw)
    raw["content_digest"] = compute_content_digest(unsigned)
    return SnapshotExchangePackageV1.model_validate(raw)


def _assert_problem(response, status_code: int) -> dict:
    assert response.status_code == status_code, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    payload = response.json()
    assert payload["status"] == status_code
    assert {"type", "title", "detail", "status"} <= set(payload)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "candidate_snapshot_id" not in serialized
    assert "building_id" not in serialized
    assert "source_id" not in serialized
    assert "Traceback" not in serialized
    assert "lease-manager-phase1" not in serialized
    return payload


def test_receiver_capability_works_without_property_scope_and_receipt_is_queryable() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _principal(
            "exchange_receiver",
            role="viewer",
            capabilities=("snapshot_exchange_receive",),
        )
        _bind_source()
        package = _package()

        response = client.put(
            f"{API_ROOT}/deliveries/{package.delivery_id}",
            headers=headers,
            json=package.model_dump(mode="json"),
        )
        assert response.status_code == 201, response.text
        receipt = response.json()
        assert receipt["status"] == "accepted"
        assert receipt["delivery_id"] == package.delivery_id
        assert response.headers["location"] == f"{API_ROOT}/receipts/{receipt['receipt_no']}"
        assert response.headers["cache-control"] == "private, no-store"
        assert not ({"id", "source_id", "building_id", "candidate_snapshot_id"} & set(receipt))

        fetched = client.get(
            f"{API_ROOT}/receipts/{receipt['receipt_no']}",
            headers=headers,
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json() == receipt
        assert fetched.headers["cache-control"] == "private, no-store"

        leader_headers = _principal(
            "portfolio_leader",
            role="viewer",
            portfolio_overview=True,
        )
        denied = client.get(
            f"{API_ROOT}/receipts/{receipt['receipt_no']}",
            headers=leader_headers,
        )
        _assert_problem(denied, 403)


def test_only_scoped_snapshot_reviewers_can_read_trimmed_source_governance_evidence() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        receiver_headers = _principal(
            "exchange_receiver",
            role="viewer",
            capabilities=("snapshot_exchange_receive",),
        )
        _bind_source()
        package = _package()
        received = client.put(
            f"{API_ROOT}/deliveries/{package.delivery_id}",
            headers=receiver_headers,
            json=package.model_dump(mode="json"),
        )
        assert received.status_code == 201, received.text
        with SessionLocal() as db:
            snapshot_id = int(db.query(SnapshotExchangeReceipt).one().candidate_snapshot_id)

        reviewer_headers = _principal(
            "source_governance_reviewer",
            role="editor",
            capabilities=("snapshot_review",),
            building_scope="DEMO_B",
        )
        reviewer_view = client.get(
            f"/api/operations/snapshot-submissions/{snapshot_id}?building=DEMO_B",
            headers=reviewer_headers,
        )
        assert reviewer_view.status_code == 200, reviewer_view.text
        evidence = reviewer_view.json()["exchange_evidence"]
        assert evidence == {
            "exchange_version": "OPS_EXCHANGE_V1",
            "source_system_code": "DEMO-B-OPS",
            "delivery_id": package.delivery_id,
            "source_publication_revision": 1,
            "source_publication_ref": "PUB-20260805-API-001",
            "correction_of_delivery_id": None,
            "correction_reason": None,
            "source_observed_at": "2026-08-05T08:00:00Z",
            "source_updated_at": "2026-08-05T07:30:00Z",
            "source_published_at": "2026-08-05T09:00:00Z",
            "workflow": package.workflow.model_dump(mode="json"),
        }
        serialized = json.dumps(evidence, ensure_ascii=False)
        assert "content_digest" not in serialized
        assert "errors" not in serialized
        assert "candidate_snapshot_id" not in serialized

        publisher_headers = _principal(
            "source_governance_publisher",
            role="editor",
            capabilities=("snapshot_publish",),
            building_scope="DEMO_B",
        )
        publisher_view = client.get(
            f"/api/operations/snapshot-submissions/{snapshot_id}?building=DEMO_B",
            headers=publisher_headers,
        )
        assert publisher_view.status_code == 200, publisher_view.text
        assert "exchange_evidence" not in publisher_view.json()


def test_http_statuses_preserve_stable_receipts_for_replay_duplicate_conflict_and_rejection() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _principal(
            "exchange_receiver",
            role="viewer",
            capabilities=("snapshot_exchange_receive",),
        )
        _bind_source()
        package = _package()
        url = f"{API_ROOT}/deliveries/{package.delivery_id}"

        first = client.put(url, headers=headers, json=package.model_dump(mode="json"))
        assert first.status_code == 201, first.text

        replay = client.put(url, headers=headers, json=package.model_dump(mode="json"))
        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()

        duplicate_package = package.model_copy(
            update={"delivery_id": "DELIVERY-20260805-API-002"}
        )
        duplicate = client.put(
            f"{API_ROOT}/deliveries/{duplicate_package.delivery_id}",
            headers=headers,
            json=duplicate_package.model_dump(mode="json"),
        )
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["status"] == "duplicate"
        assert duplicate.json()["original_receipt_no"] == first.json()["receipt_no"]

        conflict_package = _changed(package)
        conflict = client.put(
            url,
            headers=headers,
            json=conflict_package.model_dump(mode="json"),
        )
        conflict_problem = _assert_problem(conflict, 409)
        assert conflict_problem["receipt"]["status"] == "conflict"
        assert conflict_problem["receipt"]["original_receipt_no"] == first.json()["receipt_no"]
        conflict_replay = client.put(
            url,
            headers=headers,
            json=conflict_package.model_dump(mode="json"),
        )
        assert conflict_replay.status_code == 200, conflict_replay.text
        assert conflict_replay.json() == conflict_problem["receipt"]

        rejected_package = _package(delivery_id="DELIVERY-20260805-API-003").model_copy(
            update={"content_digest": "f" * 64}
        )
        rejected = client.put(
            f"{API_ROOT}/deliveries/{rejected_package.delivery_id}",
            headers=headers,
            json=rejected_package.model_dump(mode="json"),
        )
        rejected_problem = _assert_problem(rejected, 422)
        assert rejected_problem["receipt"]["status"] == "rejected"
        assert rejected_problem["receipt"]["receipt_no"]
        rejected_replay = client.put(
            f"{API_ROOT}/deliveries/{rejected_package.delivery_id}",
            headers=headers,
            json=rejected_package.model_dump(mode="json"),
        )
        assert rejected_replay.status_code == 200, rejected_replay.text
        assert rejected_replay.json() == rejected_problem["receipt"]

    with SessionLocal() as db:
        assert db.query(ImportBatch).count() == 1
        assert db.query(SnapshotExchangeReceipt).count() == 4


@pytest.mark.parametrize(
    ("username", "role", "capabilities", "portfolio_overview"),
    [
        ("ordinary_editor", "editor", (), False),
        ("snapshot_reviewer", "editor", ("snapshot_review",), False),
        ("snapshot_publisher", "admin", ("snapshot_publish",), False),
        ("portfolio_leader", "viewer", (), True),
        ("platform_admin_only", "admin", ("platform_admin",), False),
    ],
)
def test_other_business_and_platform_permissions_cannot_receive_exchange_packages(
    username: str,
    role: str,
    capabilities: tuple[str, ...],
    portfolio_overview: bool,
) -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _principal(
            username,
            role=role,
            capabilities=capabilities,
            portfolio_overview=portfolio_overview,
        )
        package = _package()
        response = client.put(
            f"{API_ROOT}/deliveries/{package.delivery_id}",
            headers=headers,
            json=package.model_dump(mode="json"),
        )
        _assert_problem(response, 403)

    with SessionLocal() as db:
        assert db.query(SnapshotExchangeReceipt).count() == 0
        assert db.query(ImportBatch).count() == 0


def test_unauthenticated_exchange_request_uses_problem_details() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        package = _package()
        response = client.put(
            f"{API_ROOT}/deliveries/{package.delivery_id}",
            json=package.model_dump(mode="json"),
        )
        _assert_problem(response, 401)


def test_delivery_path_must_match_body_before_any_business_rows_are_created() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _principal(
            "exchange_receiver",
            role="viewer",
            capabilities=("snapshot_exchange_receive",),
        )
        package = _package()
        response = client.put(
            f"{API_ROOT}/deliveries/DELIVERY-OTHER",
            headers=headers,
            json=package.model_dump(mode="json"),
        )
        _assert_problem(response, 422)

    with SessionLocal() as db:
        assert db.query(SnapshotExchangeReceipt).count() == 0
        assert db.query(ImportBatch).count() == 0


def test_duplicate_json_keys_are_rejected_before_schema_or_business_processing() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _principal(
            "exchange_receiver",
            role="viewer",
            capabilities=("snapshot_exchange_receive",),
        )
        package = _package()
        encoded = json.dumps(
            package.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        marker = f'"delivery_id":"{package.delivery_id}"'
        duplicated = encoded.replace(marker, f"{marker},{marker}", 1)
        response = client.put(
            f"{API_ROOT}/deliveries/{package.delivery_id}",
            headers={**headers, "Content-Type": "application/json"},
            content=duplicated.encode("utf-8"),
        )
        _assert_problem(response, 400)

    with SessionLocal() as db:
        assert db.query(SnapshotExchangeReceipt).count() == 0
        assert db.query(ImportBatch).count() == 0


@pytest.mark.parametrize(
    "invalid_json",
    (
        '{"oversized_integer":' + ("9" * 5000) + "}",
        '{"excessive_nesting":' + ("[" * 3000) + "0" + ("]" * 3000) + "}",
        '{"non_standard_number":NaN}',
        '{"non_standard_number":Infinity}',
        '{"non_standard_number":-Infinity}',
    ),
)
def test_json_parser_limits_are_reported_as_invalid_json(invalid_json: str) -> None:
    with TestClient(app, raise_server_exceptions=False) as client:
        _bootstrap(client)
        headers = _principal(
            "exchange_receiver",
            role="viewer",
            capabilities=("snapshot_exchange_receive",),
        )
        response = client.put(
            f"{API_ROOT}/deliveries/DELIVERY-PARSER-LIMIT",
            headers={**headers, "Content-Type": "application/json"},
            content=invalid_json.encode("utf-8"),
        )
        _assert_problem(response, 400)

    with SessionLocal() as db:
        assert db.query(SnapshotExchangeReceipt).count() == 0
        assert db.query(ImportBatch).count() == 0


def test_json_nesting_limit_ignores_brackets_inside_strings() -> None:
    with TestClient(app, raise_server_exceptions=False) as client:
        _bootstrap(client)
        headers = _principal(
            "exchange_receiver",
            role="viewer",
            capabilities=("snapshot_exchange_receive",),
        )
        response = client.put(
            f"{API_ROOT}/deliveries/DELIVERY-PARSER-STRING",
            headers={**headers, "Content-Type": "application/json"},
            content=json.dumps({"text": "[" * 300}).encode("utf-8"),
        )
        _assert_problem(response, 422)

    with SessionLocal() as db:
        assert db.query(SnapshotExchangeReceipt).count() == 0
        assert db.query(ImportBatch).count() == 0


def test_schema_errors_use_sanitized_problem_details() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _principal(
            "exchange_receiver",
            role="viewer",
            capabilities=("snapshot_exchange_receive",),
        )
        package = _package().model_dump(mode="json")
        package["unexpected_internal_option"] = True
        response = client.put(
            f"{API_ROOT}/deliveries/{package['delivery_id']}",
            headers=headers,
            json=package,
        )
        problem = _assert_problem(response, 422)
        assert "unexpected_internal_option" not in json.dumps(problem, ensure_ascii=False)


@pytest.mark.parametrize("oversized_field", ("revision", "coverage_days"))
def test_integer_storage_bounds_are_rejected_as_package_errors(
    oversized_field: str,
) -> None:
    with TestClient(app, raise_server_exceptions=False) as client:
        _bootstrap(client)
        headers = _principal(
            "exchange_receiver",
            role="viewer",
            capabilities=("snapshot_exchange_receive",),
        )
        _bind_source()
        package_model = _package()
        if oversized_field == "revision":
            package_model = package_model.model_copy(
                update={"source_publication_revision": 10**100}
            )
        else:
            metrics = list(package_model.metrics)
            metrics[0] = metrics[0].model_copy(
                update={"covered_days": 10**100, "eligible_days": 10**100}
            )
            package_model = package_model.model_copy(update={"metrics": metrics})
        package_model = package_model.model_copy(update={"content_digest": "0" * 64})
        package_model = package_model.model_copy(
            update={"content_digest": compute_content_digest(package_model)}
        )
        package = package_model.model_dump(mode="json")
        response = client.put(
            f"{API_ROOT}/deliveries/{package['delivery_id']}",
            headers=headers,
            json=package,
        )
        _assert_problem(response, 422)

    with SessionLocal() as db:
        assert db.query(SnapshotExchangeReceipt).count() == 0
        assert db.query(ImportBatch).count() == 0


@pytest.mark.parametrize(
    ("field", "spoofed_value"),
    (
        ("source_system_code", "UNREGISTERED-SOURCE"),
        ("building_code", "DEMO_D"),
    ),
)
def test_source_identity_is_resolved_from_server_binding_and_cannot_be_spoofed(
    field: str,
    spoofed_value: str,
) -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _principal(
            "exchange_receiver",
            role="viewer",
            capabilities=("snapshot_exchange_receive",),
        )
        _bind_source()
        raw = _package().model_dump(mode="json")
        raw[field] = spoofed_value
        package = _resign(raw)
        response = client.put(
            f"{API_ROOT}/deliveries/{package.delivery_id}",
            headers=headers,
            json=package.model_dump(mode="json"),
        )
        _assert_problem(response, 422)

    with SessionLocal() as db:
        assert db.query(SnapshotExchangeReceipt).count() == 0
        assert db.query(ImportBatch).count() == 0


@pytest.mark.parametrize("deactivate", ("binding", "source"))
def test_inactive_source_registration_is_fail_closed(deactivate: str) -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _principal(
            "exchange_receiver",
            role="viewer",
            capabilities=("snapshot_exchange_receive",),
        )
        _bind_source()
        with SessionLocal() as db:
            if deactivate == "binding":
                db.query(SnapshotExchangeSourceBinding).one().is_active = False
            else:
                db.query(DataSource).filter(DataSource.source_type == "external_api").one().is_active = False
            db.commit()
        package = _package()
        response = client.put(
            f"{API_ROOT}/deliveries/{package.delivery_id}",
            headers=headers,
            json=package.model_dump(mode="json"),
        )
        _assert_problem(response, 422)

    with SessionLocal() as db:
        assert db.query(SnapshotExchangeReceipt).count() == 0
        assert db.query(ImportBatch).count() == 0


def test_sqlite_contention_returns_retryable_problem_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_args, **_kwargs):
        raise exchange_service.SnapshotExchangeUnavailable("simulated contention")

    monkeypatch.setattr(exchange_service, "receive_snapshot_package", unavailable)
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _principal(
            "exchange_receiver",
            role="viewer",
            capabilities=("snapshot_exchange_receive",),
        )
        package = _package()
        response = client.put(
            f"{API_ROOT}/deliveries/{package.delivery_id}",
            headers=headers,
            json=package.model_dump(mode="json"),
        )
        _assert_problem(response, 503)
        assert response.headers["retry-after"] == "1"


def test_unexpected_exchange_integrity_error_is_not_misreported_as_delivery_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_integrity(*_args, **_kwargs):
        raise IntegrityError(
            "INSERT INTO internal_table ...",
            {},
            Exception("FOREIGN KEY constraint failed"),
        )

    monkeypatch.setattr(
        exchange_service,
        "receive_snapshot_package",
        _raise_integrity,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        _bootstrap(client)
        headers = _principal(
            "exchange_receiver",
            role="viewer",
            capabilities=("snapshot_exchange_receive",),
        )
        package = _package()
        response = client.put(
            f"{API_ROOT}/deliveries/{package.delivery_id}",
            headers=headers,
            json=package.model_dump(mode="json"),
        )
        problem = _assert_problem(response, 500)
        assert "FOREIGN KEY" not in json.dumps(problem, ensure_ascii=False)


def test_maintenance_mode_keeps_the_exchange_problem_contract_and_no_store() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        with maintenance_mode():
            response = client.get(f"{API_ROOT}/receipts/RX-MAINTENANCE")
        _assert_problem(response, 503)
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["retry-after"] == "1"


def test_missing_receipt_is_not_disclosed_as_an_internal_database_key() -> None:
    with TestClient(app) as client:
        _bootstrap(client)
        headers = _principal(
            "exchange_receiver",
            role="viewer",
            capabilities=("snapshot_exchange_receive",),
        )
        response = client.get(
            f"{API_ROOT}/receipts/RX-NOT-FOUND",
            headers=headers,
        )
        _assert_problem(response, 404)
