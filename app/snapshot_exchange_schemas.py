from __future__ import annotations

import calendar
from datetime import date
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import unicodedata
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    model_validator,
)


def _reject_numeric_code(value: str) -> str:
    if value.lstrip("+-").isdigit():
        raise ValueError("必须使用稳定代码，不能使用数字 ID")
    return value


def _parse_utc_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def _strict_iso_date(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("日期必须使用 YYYY-MM-DD 字符串")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("日期必须使用 YYYY-MM-DD 字符串") from exc
    if parsed.isoformat() != value:
        raise ValueError("日期必须使用 YYYY-MM-DD 字符串")
    return parsed


def _strings_in(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings_in(item)


StableCode = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
    AfterValidator(_reject_numeric_code),
]
ExternalReference = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=150,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    ),
]
StableSourceReference = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=150,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    ),
    AfterValidator(_reject_numeric_code),
]
UtcTimestamp = Annotated[
    str,
    StringConstraints(
        min_length=20,
        max_length=20,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
    ),
]
CanonicalDecimal = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=16,
        pattern=r"^(?:0|[1-9]\d{0,8})(?:\.\d{0,5}[1-9])?$",
    ),
]
DigestHex = Annotated[
    str,
    StringConstraints(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]
StrictIsoDate = Annotated[date, BeforeValidator(_strict_iso_date)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SnapshotExchangeWorkflowStep(_StrictModel):
    actor_ref: ExternalReference
    occurred_at: UtcTimestamp


class SnapshotExchangeWorkflow(_StrictModel):
    submitted: SnapshotExchangeWorkflowStep
    reviewed: SnapshotExchangeWorkflowStep
    published: SnapshotExchangeWorkflowStep

    @model_validator(mode="after")
    def require_independent_ordered_actors(self) -> "SnapshotExchangeWorkflow":
        actors = {
            self.submitted.actor_ref,
            self.reviewed.actor_ref,
            self.published.actor_ref,
        }
        if len(actors) != 3:
            raise ValueError("来源提交、复核和发布责任引用必须分别独立")
        times = tuple(
            _parse_utc_timestamp(item.occurred_at)
            for item in (self.submitted, self.reviewed, self.published)
        )
        if times != tuple(sorted(times)):
            raise ValueError("来源提交、复核和发布时间顺序不合法")
        return self


class SnapshotExchangeMetricV1(_StrictModel):
    code: StableCode
    value: CanonicalDecimal | None = None
    numerator: CanonicalDecimal | None
    denominator: CanonicalDecimal | None = None
    covered_area_sqm: CanonicalDecimal | None = None
    eligible_area_sqm: CanonicalDecimal | None = None
    covered_days: StrictInt = Field(ge=0, le=31)
    eligible_days: StrictInt = Field(ge=0, le=31)
    estimate_share: CanonicalDecimal | None = None
    estimate_method: str = Field(default="", max_length=2000)
    completeness_status: Literal["unknown", "partial", "complete"]
    freshness_status: Literal["unknown", "current", "stale"]
    calculation_version: StableCode
    source_batch_ref: StableSourceReference
    source_record_refs: list[StableSourceReference] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_canonical_reference_order(self) -> "SnapshotExchangeMetricV1":
        if self.source_record_refs != sorted(set(self.source_record_refs)):
            raise ValueError("source_record_refs 必须去重并按代码升序排列")
        covered_area = Decimal(self.covered_area_sqm) if self.covered_area_sqm is not None else None
        eligible_area = Decimal(self.eligible_area_sqm) if self.eligible_area_sqm is not None else None
        if covered_area is not None and eligible_area is not None and covered_area > eligible_area:
            raise ValueError("已覆盖面积不能大于应覆盖面积")
        if self.covered_days > self.eligible_days:
            raise ValueError("已覆盖天数不能大于应覆盖天数")
        estimate_share = Decimal(self.estimate_share) if self.estimate_share is not None else None
        if estimate_share is not None and estimate_share > Decimal(1):
            raise ValueError("估算比例必须在 0 到 1 之间")
        if estimate_share is not None and estimate_share > 0 and not self.estimate_method.strip():
            raise ValueError("含估算时必须说明估算方法")
        if self.estimate_method and estimate_share in (None, Decimal(0)):
            raise ValueError("记录估算方法时必须同时记录非零估算比例")
        if self.numerator is None:
            if self.value is not None or self.denominator is not None:
                raise ValueError("未知指标的数值、分子和分母必须同时为空")
            if self.estimate_share is not None or self.estimate_method:
                raise ValueError("未知指标不能声明估算结果")
            if self.completeness_status != "unknown" or self.freshness_status != "unknown":
                raise ValueError("未知指标的完整性和时效性必须同时为 unknown")
        else:
            positive_coverage = (
                covered_area is not None
                and eligible_area is not None
                and eligible_area > 0
                and covered_area > 0
            ) or (self.eligible_days > 0 and self.covered_days > 0)
            if (
                self.completeness_status in {"partial", "complete"}
                and not positive_coverage
            ):
                raise ValueError("已知指标必须具有正数覆盖依据")
            if self.completeness_status == "complete":
                if (
                    covered_area is not None
                    and eligible_area is not None
                    and covered_area != eligible_area
                ):
                    raise ValueError("完整指标的面积覆盖必须达到应覆盖面积")
                if self.covered_days != self.eligible_days:
                    raise ValueError("完整指标的时间覆盖必须达到应覆盖天数")
        return self


class SnapshotExchangePackageV1(_StrictModel):
    exchange_version: Literal["OPS_EXCHANGE_V1"]
    delivery_id: StableCode
    source_system_code: StableCode
    building_code: StableCode
    metric_definition_version: Literal["OPS_V1"]
    period_start: StrictIsoDate
    period_end: StrictIsoDate
    as_of_date: StrictIsoDate
    source_observed_at: UtcTimestamp
    source_updated_at: UtcTimestamp | None = None
    source_published_at: UtcTimestamp
    source_publication_revision: StrictInt = Field(gt=0, le=2_147_483_647)
    source_publication_ref: ExternalReference
    correction_of_delivery_id: StableCode | None = None
    correction_reason: str | None = Field(default=None, min_length=1, max_length=2000)
    data_classification: Literal["simulated"]
    digest_profile: Literal["OPS_CANONICAL_V1"]
    digest_algorithm: Literal["sha-256"]
    content_digest: DigestHex
    metrics: list[SnapshotExchangeMetricV1] = Field(min_length=1, max_length=100)
    workflow: SnapshotExchangeWorkflow

    @model_validator(mode="after")
    def require_canonical_metric_order(self) -> "SnapshotExchangePackageV1":
        codes = [item.code for item in self.metrics]
        if codes != sorted(set(codes)):
            raise ValueError("metrics 必须按指标代码升序排列且不能重复")

        expected_start = self.as_of_date.replace(day=1)
        expected_end = self.as_of_date.replace(
            day=calendar.monthrange(self.as_of_date.year, self.as_of_date.month)[1]
        )
        if self.period_start != expected_start or self.period_end != expected_end:
            raise ValueError("OPS_V1 交换包期间必须是统计日所在自然月")

        observed_at = _parse_utc_timestamp(self.source_observed_at)
        published_at = _parse_utc_timestamp(self.source_published_at)
        if self.source_updated_at is not None:
            updated_at = _parse_utc_timestamp(self.source_updated_at)
            if updated_at > observed_at:
                raise ValueError("来源更新时间不能晚于来源观测时间")
        if observed_at > published_at:
            raise ValueError("来源观测时间不能晚于来源发布时间")
        submitted_at = _parse_utc_timestamp(self.workflow.submitted.occurred_at)
        if observed_at > submitted_at:
            raise ValueError("来源提交时间不能早于来源观测时间")
        if self.workflow.published.occurred_at != self.source_published_at:
            raise ValueError("来源责任链发布时间必须与包头发布时间一致")

        correction_fields = (
            self.correction_of_delivery_id is not None,
            self.correction_reason is not None,
        )
        if correction_fields[0] != correction_fields[1]:
            raise ValueError("更正投递引用和更正原因必须同时提供")
        if self.correction_reason is not None and (
            not self.correction_reason.strip() or self.correction_reason != self.correction_reason.strip()
        ):
            raise ValueError("更正原因不能为空或包含首尾空格")
        if any(
            value != unicodedata.normalize("NFC", value)
            for value in _strings_in(self.model_dump(mode="python"))
        ):
            raise ValueError("OPS_CANONICAL_V1 的全部文本必须使用 Unicode NFC")
        return self


_DIGEST_EXCLUDED_FIELDS = frozenset(
    {"delivery_id", "digest_profile", "digest_algorithm", "content_digest"}
)


def canonical_business_json(package: SnapshotExchangePackageV1) -> str:
    business_content = package.model_dump(
        mode="json",
        exclude=_DIGEST_EXCLUDED_FIELDS,
    )
    return json.dumps(
        business_content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def compute_content_digest(package: SnapshotExchangePackageV1) -> str:
    return hashlib.sha256(canonical_business_json(package).encode("utf-8")).hexdigest()


class SnapshotExchangeIssue(_StrictModel):
    code: StableCode
    field: str | None = Field(default=None, max_length=200)
    row: StrictInt | None = Field(default=None, ge=1)
    message: str = Field(min_length=1, max_length=500)


class SnapshotExchangeReceiptOut(_StrictModel):
    receipt_version: Literal["OPS_EXCHANGE_RECEIPT_V1"]
    receipt_no: ExternalReference
    exchange_version: Literal["OPS_EXCHANGE_V1"]
    source_system_code: StableCode
    building_code: StableCode
    delivery_id: StableCode
    digest_profile: Literal["OPS_CANONICAL_V1"]
    digest_algorithm: Literal["sha-256"]
    server_content_digest: DigestHex
    source_publication_revision: StrictInt = Field(gt=0)
    status: Literal["accepted", "duplicate", "rejected", "conflict"]
    first_received_at: UtcTimestamp
    original_receipt_no: ExternalReference | None = None
    correction_of_receipt_no: ExternalReference | None = None
    candidate_batch_ref: ExternalReference | None = None
    center_status: Literal["pending_review", "none"]
    errors: list[SnapshotExchangeIssue] = Field(default_factory=list, max_length=100)
