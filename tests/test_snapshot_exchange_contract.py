from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.snapshot_exchange_schemas import (
    SnapshotExchangePackageV1,
    canonical_business_json,
    compute_content_digest,
)


def _valid_package() -> dict:
    return {
        "exchange_version": "OPS_EXCHANGE_V1",
        "delivery_id": "DELIVERY-20260805-001",
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
        "source_publication_ref": "PUB-20260805-001",
        "correction_of_delivery_id": None,
        "correction_reason": None,
        "data_classification": "simulated",
        "digest_profile": "OPS_CANONICAL_V1",
        "digest_algorithm": "sha-256",
        "content_digest": "0" * 64,
        "metrics": [
            {
                "code": "area.rentable_sqm",
                "value": "100",
                "numerator": "100",
                "denominator": None,
                "covered_area_sqm": "100",
                "eligible_area_sqm": "100",
                "covered_days": 5,
                "eligible_days": 31,
                "estimate_share": "0",
                "estimate_method": "",
                "completeness_status": "partial",
                "freshness_status": "current",
                "calculation_version": "ops-source-v1",
                "source_batch_ref": "BATCH-20260805-001",
                "source_record_refs": ["AREA-001"],
            }
        ],
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


def _occupancy_metric() -> dict:
    return {
        "code": "occupancy.area_rate",
        "value": "80",
        "numerator": "80",
        "denominator": "100",
        "covered_area_sqm": "100",
        "eligible_area_sqm": "100",
        "covered_days": 5,
        "eligible_days": 31,
        "estimate_share": "0",
        "estimate_method": "",
        "completeness_status": "partial",
        "freshness_status": "current",
        "calculation_version": "ops-source-v1",
        "source_batch_ref": "BATCH-20260805-001",
        "source_record_refs": ["OCC-001", "OCC-002"],
    }


def test_exchange_and_metric_versions_are_independent_and_internal_ids_are_forbidden() -> None:
    package = SnapshotExchangePackageV1.model_validate(_valid_package())
    assert package.exchange_version == "OPS_EXCHANGE_V1"
    assert package.metric_definition_version == "OPS_V1"

    for field, bad_value in (
        ("exchange_version", "OPS_V1"),
        ("metric_definition_version", "OPS_EXCHANGE_V1"),
    ):
        payload = deepcopy(_valid_package())
        payload[field] = bad_value
        with pytest.raises(ValidationError):
            SnapshotExchangePackageV1.model_validate(payload)

    for field, bad_value in (
        ("delivery_id", ""),
        ("delivery_id", "D" * 101),
        ("delivery_id", "123"),
        ("source_system_code", "123"),
        ("building_code", "456"),
    ):
        payload = deepcopy(_valid_package())
        payload[field] = bad_value
        with pytest.raises(ValidationError):
            SnapshotExchangePackageV1.model_validate(payload)


def test_exchange_package_requires_canonical_metric_and_reference_order() -> None:
    payload = _valid_package()
    payload["metrics"].append(_occupancy_metric())
    SnapshotExchangePackageV1.model_validate(payload)

    metrics_reversed = deepcopy(payload)
    metrics_reversed["metrics"].reverse()
    with pytest.raises(ValidationError):
        SnapshotExchangePackageV1.model_validate(metrics_reversed)

    references_reversed = deepcopy(payload)
    references_reversed["metrics"][1]["source_record_refs"].reverse()
    with pytest.raises(ValidationError):
        SnapshotExchangePackageV1.model_validate(references_reversed)

    duplicate_metric = deepcopy(payload)
    duplicate_metric["metrics"][1]["code"] = "area.rentable_sqm"
    with pytest.raises(ValidationError):
        SnapshotExchangePackageV1.model_validate(duplicate_metric)

    for field, bad_value in (
        ("source_batch_ref", "123"),
        ("source_record_refs", ["456"]),
    ):
        numeric_reference = deepcopy(_valid_package())
        numeric_reference["metrics"][0][field] = bad_value
        with pytest.raises(ValidationError):
            SnapshotExchangePackageV1.model_validate(numeric_reference)


def test_exchange_package_rejects_ambiguous_period_workflow_and_correction_chain() -> None:
    SnapshotExchangePackageV1.model_validate(_valid_package())

    mutations = (
        ("period_end", "2026-08-30"),
        ("as_of_date", "2026-09-01"),
        ("source_updated_at", "2026-08-05T08:30:00Z"),
        ("source_observed_at", "2026-08-05T09:30:00Z"),
    )
    for field, bad_value in mutations:
        payload = deepcopy(_valid_package())
        payload[field] = bad_value
        with pytest.raises(ValidationError):
            SnapshotExchangePackageV1.model_validate(payload)

    for ambiguous_date in (
        "2026-08-05T00:00:00",
        "2026-08-05T00:00:00Z",
        1785888000,
    ):
        payload = deepcopy(_valid_package())
        payload["as_of_date"] = ambiguous_date
        with pytest.raises(ValidationError):
            SnapshotExchangePackageV1.model_validate(payload)

    duplicate_actor = deepcopy(_valid_package())
    duplicate_actor["workflow"]["reviewed"]["actor_ref"] = "user:submitter"
    with pytest.raises(ValidationError):
        SnapshotExchangePackageV1.model_validate(duplicate_actor)

    reversed_workflow = deepcopy(_valid_package())
    reversed_workflow["workflow"]["reviewed"]["occurred_at"] = "2026-08-05T08:05:00Z"
    with pytest.raises(ValidationError):
        SnapshotExchangePackageV1.model_validate(reversed_workflow)

    publication_mismatch = deepcopy(_valid_package())
    publication_mismatch["workflow"]["published"]["occurred_at"] = "2026-08-05T09:01:00Z"
    with pytest.raises(ValidationError):
        SnapshotExchangePackageV1.model_validate(publication_mismatch)

    workflow_before_source = deepcopy(_valid_package())
    workflow_before_source["workflow"]["submitted"]["occurred_at"] = (
        "2026-08-05T07:00:00Z"
    )
    with pytest.raises(ValidationError):
        SnapshotExchangePackageV1.model_validate(workflow_before_source)

    half_correction = deepcopy(_valid_package())
    half_correction["correction_of_delivery_id"] = "DELIVERY-20260705-001"
    with pytest.raises(ValidationError):
        SnapshotExchangePackageV1.model_validate(half_correction)


def test_ops_canonical_v1_has_a_fixed_cross_runtime_digest_vector() -> None:
    package = SnapshotExchangePackageV1.model_validate(_valid_package())
    expected_canonical = (
        '{"as_of_date":"2026-08-05","building_code":"DEMO_B",'
        '"correction_of_delivery_id":null,"correction_reason":null,'
        '"data_classification":"simulated","exchange_version":"OPS_EXCHANGE_V1",'
        '"metric_definition_version":"OPS_V1","metrics":'
        '[{"calculation_version":"ops-source-v1","code":"area.rentable_sqm",'
        '"completeness_status":"partial","covered_area_sqm":"100","covered_days":5,'
        '"denominator":null,"eligible_area_sqm":"100","eligible_days":31,'
        '"estimate_method":"","estimate_share":"0","freshness_status":"current",'
        '"numerator":"100","source_batch_ref":"BATCH-20260805-001",'
        '"source_record_refs":["AREA-001"],"value":"100"}],'
        '"period_end":"2026-08-31","period_start":"2026-08-01",'
        '"source_observed_at":"2026-08-05T08:00:00Z",'
        '"source_publication_ref":"PUB-20260805-001","source_publication_revision":1,'
        '"source_published_at":"2026-08-05T09:00:00Z",'
        '"source_system_code":"DEMO-B-OPS",'
        '"source_updated_at":"2026-08-05T07:30:00Z",'
        '"workflow":{"published":{"actor_ref":"user:publisher",'
        '"occurred_at":"2026-08-05T09:00:00Z"},'
        '"reviewed":{"actor_ref":"user:reviewer",'
        '"occurred_at":"2026-08-05T08:30:00Z"},'
        '"submitted":{"actor_ref":"user:submitter",'
        '"occurred_at":"2026-08-05T08:10:00Z"}}}'
    )
    assert canonical_business_json(package) == expected_canonical
    assert compute_content_digest(package) == (
        "bc4a0448c84190251dd5eedc4d37a287499dc099e672e6ed653fdbb25b2120bb"
    )

    replay = package.model_copy(
        update={"delivery_id": "DELIVERY-RETRY-999", "content_digest": "f" * 64}
    )
    assert compute_content_digest(replay) == compute_content_digest(package)

    changed = deepcopy(_valid_package())
    changed["metrics"][0]["value"] = "101"
    changed["metrics"][0]["numerator"] = "101"
    assert compute_content_digest(SnapshotExchangePackageV1.model_validate(changed)) != (
        compute_content_digest(package)
    )


def test_exchange_metric_keeps_zero_distinct_and_rejects_noncanonical_numbers() -> None:
    zero = _valid_package()
    zero["metrics"][0]["value"] = "0"
    zero["metrics"][0]["numerator"] = "0"
    parsed = SnapshotExchangePackageV1.model_validate(zero)
    assert parsed.metrics[0].value == "0"
    assert parsed.metrics[0].numerator == "0"

    bad_values = (0.0, "0.0", "01", "1e2", "-0")
    for bad_value in bad_values:
        payload = deepcopy(_valid_package())
        payload["metrics"][0]["value"] = bad_value
        with pytest.raises(ValidationError):
            SnapshotExchangePackageV1.model_validate(payload)

    bad_coverage = deepcopy(_valid_package())
    bad_coverage["metrics"][0]["covered_area_sqm"] = "101"
    with pytest.raises(ValidationError):
        SnapshotExchangePackageV1.model_validate(bad_coverage)

    bad_days = deepcopy(_valid_package())
    bad_days["metrics"][0]["covered_days"] = 32
    with pytest.raises(ValidationError):
        SnapshotExchangePackageV1.model_validate(bad_days)

    missing_estimate_method = deepcopy(_valid_package())
    missing_estimate_method["metrics"][0]["estimate_share"] = "0.1"
    with pytest.raises(ValidationError):
        SnapshotExchangePackageV1.model_validate(missing_estimate_method)


def test_exchange_metric_precision_and_coverage_match_center_storage_rules() -> None:
    maximum = deepcopy(_valid_package())
    maximum["metrics"][0]["value"] = "999999999.999999"
    maximum["metrics"][0]["numerator"] = "999999999.999999"
    SnapshotExchangePackageV1.model_validate(maximum)

    for unsafe_value in (
        "9999999999.999999",
        "123456789012345678901234567890",
    ):
        too_large = deepcopy(_valid_package())
        too_large["metrics"][0]["value"] = unsafe_value
        too_large["metrics"][0]["numerator"] = unsafe_value
        with pytest.raises(ValidationError):
            SnapshotExchangePackageV1.model_validate(too_large)

    complete_without_coverage = deepcopy(_valid_package())
    metric = complete_without_coverage["metrics"][0]
    metric["covered_area_sqm"] = "0"
    metric["eligible_area_sqm"] = "100"
    metric["covered_days"] = 0
    metric["eligible_days"] = 31
    metric["completeness_status"] = "complete"
    with pytest.raises(ValidationError):
        SnapshotExchangePackageV1.model_validate(complete_without_coverage)

    oversized_revision = deepcopy(_valid_package())
    oversized_revision["source_publication_revision"] = 2_147_483_648
    with pytest.raises(ValidationError):
        SnapshotExchangePackageV1.model_validate(oversized_revision)

    oversized_days = deepcopy(_valid_package())
    oversized_days["metrics"][0]["covered_days"] = 32
    oversized_days["metrics"][0]["eligible_days"] = 32
    with pytest.raises(ValidationError):
        SnapshotExchangePackageV1.model_validate(oversized_days)


def test_exchange_metric_represents_unknown_explicitly_instead_of_turning_it_into_zero() -> None:
    unknown = _valid_package()
    metric = unknown["metrics"][0]
    metric.update(
        {
            "value": None,
            "numerator": None,
            "denominator": None,
            "covered_area_sqm": None,
            "covered_days": 0,
            "estimate_share": None,
            "completeness_status": "unknown",
            "freshness_status": "unknown",
        }
    )
    parsed = SnapshotExchangePackageV1.model_validate(unknown)
    assert parsed.metrics[0].value is None
    assert parsed.metrics[0].numerator is None

    for field, bad_value in (
        ("value", "0"),
        ("denominator", "1"),
        ("estimate_share", "0"),
        ("completeness_status", "partial"),
        ("freshness_status", "current"),
    ):
        invalid = deepcopy(unknown)
        invalid["metrics"][0][field] = bad_value
        with pytest.raises(ValidationError):
            SnapshotExchangePackageV1.model_validate(invalid)


def test_digest_profile_requires_nfc_text_instead_of_silent_unicode_rewriting() -> None:
    normalized = deepcopy(_valid_package())
    normalized["metrics"][0]["estimate_share"] = "0.1"
    normalized["metrics"][0]["estimate_method"] = "méthode"
    SnapshotExchangePackageV1.model_validate(normalized)

    decomposed = deepcopy(normalized)
    decomposed["metrics"][0]["estimate_method"] = "me\u0301thode"
    with pytest.raises(ValidationError):
        SnapshotExchangePackageV1.model_validate(decomposed)

    for forbidden_field in ("source_id", "building_id"):
        payload = deepcopy(_valid_package())
        payload[forbidden_field] = 1
        with pytest.raises(ValidationError):
            SnapshotExchangePackageV1.model_validate(payload)
