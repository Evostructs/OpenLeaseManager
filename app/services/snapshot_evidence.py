from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import (
    BuildingMetricFact,
    BuildingOperatingSnapshot,
    DataSource,
    ImportBatch,
    MetricDefinition,
    SourceRecord,
    User,
)
from app.ops_v1 import OPS_V1_DEFINITIONS, SUBMITTABLE_CODES
from app.services.metric_definitions import published_ops_v1_by_code
from app.services.property_scope import SelectedPropertyScope
from app.services.unified_operating_snapshot import build_unified_operating_snapshot


EVIDENCE_CONTRACT_VERSION = "snapshot-evidence-v1"


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _displayed_contributions(displayed: dict) -> list[dict]:
    contributions = displayed.get("contributions")
    if isinstance(contributions, list):
        return contributions
    building = (displayed.get("scope") or {}).get("building")
    if not isinstance(building, dict):
        return []
    return [
        {
            "building": building,
            "snapshot_id": None,
            "revision": None,
            "publication_status": "live_adapter",
            "published_at": None,
            "reason": "当天明细按 OPS_V1 实时适配",
            "metrics": displayed.get("metrics") or {},
        }
    ]


def _definition_payload(definition: MetricDefinition, aggregate: dict) -> dict:
    return {
        "code": definition.code,
        "name": definition.name,
        "unit": definition.unit,
        "formula": definition.business_definition,
        "definition_hash": definition.definition_hash,
        "definition_version": definition.version,
        "numerator_rule": definition.numerator_rule,
        "denominator_rule": definition.denominator_rule,
        "inclusion_rule": definition.inclusion_rule,
        "exclusion_rule": definition.exclusion_rule,
        "aggregation_rule": definition.aggregation_rule,
        "coverage_rule": definition.coverage_rule,
        "estimation_policy": definition.estimation_policy,
        "freshness_policy": definition.freshness_policy,
        "owner": definition.owner,
        "aggregate": {
            "value": aggregate.get("value"),
            "numerator": aggregate.get("numerator"),
            "denominator": aggregate.get("denominator"),
            "period_start": aggregate.get("period_start"),
            "period_end": aggregate.get("period_end"),
            "as_of_date": aggregate.get("as_of_date"),
            "calculation_version": aggregate.get("calculation_version"),
            "coverage": aggregate.get("coverage") or {},
            "unavailable_reason": aggregate.get("unavailable_reason"),
        },
    }


def _actor_payload(actor: User | None) -> dict | None:
    if actor is None:
        return None
    return {
        "id": actor.id,
        "name": str(actor.display_name or "").strip() or f"用户 #{actor.id}",
    }


def _snapshot_payload(
    snapshot: BuildingOperatingSnapshot,
    *,
    actor_by_id: dict[int, User],
) -> dict:
    return {
        "id": snapshot.id,
        "revision": snapshot.revision,
        "validation_status": snapshot.validation_status,
        "publication_status": snapshot.publication_status,
        "is_current": bool(snapshot.is_current),
        "period_start": _iso(snapshot.period_start),
        "period_end": _iso(snapshot.period_end),
        "as_of_date": _iso(snapshot.as_of_date),
        "submitted_at": _iso(snapshot.submitted_at),
        "submitted_by": _actor_payload(actor_by_id.get(snapshot.submitted_by)),
        "reviewed_at": _iso(snapshot.reviewed_at),
        "reviewed_by": _actor_payload(actor_by_id.get(snapshot.reviewed_by)),
        "published_at": _iso(snapshot.published_at),
        "published_by": _actor_payload(actor_by_id.get(snapshot.published_by)),
        "supersedes_id": snapshot.supersedes_id,
        "data_classification": "simulated",
    }


def _fact_payload(
    fact: BuildingMetricFact,
    *,
    definition: MetricDefinition,
    contribution_metric: dict,
) -> dict:
    coverage = contribution_metric.get("coverage") or {}
    return {
        "id": fact.id,
        "value": contribution_metric.get("value"),
        "numerator": contribution_metric.get("numerator"),
        "denominator": contribution_metric.get("denominator"),
        "unit": definition.unit,
        "coverage": coverage,
        "estimate_share": fact.estimate_share,
        "estimate_method": fact.estimate_method,
        "data_depth": fact.data_depth,
        "completeness_status": fact.completeness_status,
        "freshness_status": fact.freshness_status,
        "validation_status": fact.validation_status,
        "publication_status": fact.publication_status,
        "calculation_version": fact.calculation_version,
        "unavailable_reason": contribution_metric.get("unavailable_reason"),
        "aggregation_eligible": contribution_metric.get("aggregation_eligible", True),
        "aggregation_excluded_reason": contribution_metric.get("aggregation_excluded_reason"),
        "data_classification": "simulated",
    }


def _live_fact_payload(metric: dict, *, unit: str) -> dict:
    coverage = metric.get("coverage") or {}
    return {
        "id": None,
        "value": metric.get("value"),
        "numerator": metric.get("numerator"),
        "denominator": metric.get("denominator"),
        "unit": unit,
        "coverage": coverage,
        "estimate_share": coverage.get("estimate_share"),
        "estimate_method": "",
        "data_depth": coverage.get("data_depth", "detail"),
        "completeness_status": coverage.get("completeness_status", "unknown"),
        "freshness_status": coverage.get("freshness_status", "unknown"),
        "validation_status": coverage.get("validation_status", "valid"),
        "publication_status": "live_adapter",
        "calculation_version": metric.get("calculation_version"),
        "unavailable_reason": metric.get("unavailable_reason"),
        "aggregation_eligible": metric.get("aggregation_eligible", True),
        "aggregation_excluded_reason": metric.get("aggregation_excluded_reason"),
        "data_classification": "simulated",
    }


def _source_payload(source: DataSource) -> dict:
    return {
        "id": source.id,
        "source_type": source.source_type,
        "name": source.name,
        "owner": source.owner,
        "cadence": source.cadence,
        "freshness_rule": source.freshness_rule,
        "is_active": bool(source.is_active),
        "data_classification": "simulated",
    }


def _batch_payload(
    batch: ImportBatch | None,
    *,
    actor_by_id: dict[int, User],
) -> dict | None:
    if batch is None:
        return None
    return {
        "id": batch.id,
        "status": batch.status,
        "source_file_name": batch.source_file_name,
        "external_batch_no": batch.external_batch_no,
        "period": batch.period,
        "as_of_date": _iso(batch.as_of_date),
        "source_observed_at": _iso(batch.source_observed_at),
        "source_updated_at": _iso(batch.source_updated_at),
        "received_at": _iso(batch.received_at),
        "ingested_at": _iso(batch.ingested_at),
        "published_at": _iso(batch.published_at),
        "file_checksum": batch.file_checksum,
        "parser_version": batch.parser_version,
        "actor": _actor_payload(actor_by_id.get(batch.actor_id)),
        "row_count": batch.row_count,
        "accepted_count": batch.accepted_count,
        "rejected_count": batch.rejected_count,
        "reviewed_by": _actor_payload(actor_by_id.get(batch.reviewed_by)),
        "reviewed_at": _iso(batch.reviewed_at),
        "correction_reason": batch.correction_reason,
        "supersedes_id": batch.supersedes_id,
        "data_classification": "simulated",
    }


def _record_payload(record: SourceRecord | None) -> dict | None:
    if record is None:
        return None
    return {
        "id": record.id,
        "external_record_id": record.external_record_id,
        "row_no": record.row_no,
        "record_checksum": record.record_checksum,
        "status": record.status,
        "source_ref": record.source_ref,
        "data_classification": "simulated",
    }


def _available_metrics(
    definitions: dict[str, MetricDefinition],
    *,
    displayed_metrics: dict[str, dict],
) -> list[dict]:
    return [
        {
            "code": spec["code"],
            "name": definitions[spec["code"]].name,
            "unit": definitions[spec["code"]].unit,
            "scope_status": (
                "provided" if spec["code"] in displayed_metrics else "not_provided"
            ),
            "scope_note": (
                (displayed_metrics.get(spec["code"]) or {}).get("unavailable_reason")
                if spec["code"] in displayed_metrics
                else "当前明细适配器尚未提供该指标"
            ),
        }
        for spec in OPS_V1_DEFINITIONS
        if spec["code"] in SUBMITTABLE_CODES
    ]


def _adapter_missing_aggregate(
    definition: MetricDefinition,
    *,
    effective_date: date,
    scope: SelectedPropertyScope,
) -> dict:
    reason = "当前明细适配器尚未提供该指标"
    authorized_count = len(tuple(scope.building_ids))
    return {
        "code": definition.code,
        "name": definition.name,
        "value": None,
        "numerator": None,
        "denominator": None,
        "unit": definition.unit,
        "formula": definition.business_definition,
        "period_start": None,
        "period_end": None,
        "as_of_date": effective_date.isoformat(),
        "definition_version": definition.version,
        "definition_hash": definition.definition_hash,
        "calculation_version": None,
        "coverage": {
            "portfolio_buildings": authorized_count,
            "authorized_buildings": authorized_count,
            "eligible_buildings": 0,
            "included_buildings": 0,
            "covered_area_sqm": None,
            "known_total_area_sqm": None,
            "covered_days": None,
            "eligible_days": None,
            "source_mix": ["detail"],
            "source_ids": [],
            "batch_ids": [],
            "source_record_ids": [],
            "data_depth": "detail",
            "completeness_status": "missing",
            "freshness_status": "unknown",
            "validation_status": "pending",
            "publication_status": "draft",
            "estimate_share": None,
            "excluded_buildings": [],
        },
        "unavailable_reason": reason,
    }


def build_snapshot_evidence(
    db: Session,
    *,
    scope: SelectedPropertyScope,
    metric_code: str,
    as_of_date: date | None = None,
) -> dict:
    normalized_code = str(metric_code or "").strip()
    if normalized_code not in SUBMITTABLE_CODES:
        raise HTTPException(422, "指标不属于 OPS_V1 可追溯集合")

    definitions = published_ops_v1_by_code(db)
    definition = definitions.get(normalized_code)
    if definition is None:
        raise HTTPException(409, "OPS_V1 指标定义不完整")

    displayed = build_unified_operating_snapshot(
        db,
        scope=scope,
        as_of_date=as_of_date,
        include_trend=False,
        queue_limit=0,
        risk_limit=0,
    )
    effective_date = date.fromisoformat(displayed["as_of_date"])
    is_portfolio_scope = (displayed.get("scope") or {}).get("scope_type") == "portfolio"
    displayed_metrics = displayed.get("metrics") or {}
    aggregate = displayed_metrics.get(normalized_code)
    if aggregate is None:
        aggregate = _adapter_missing_aggregate(
            definition,
            effective_date=effective_date,
            scope=scope,
        )

    contributions = _displayed_contributions(displayed)
    snapshot_ids = tuple(
        int(item["snapshot_id"])
        for item in contributions
        if item.get("snapshot_id") is not None
    )
    snapshots = (
        db.query(BuildingOperatingSnapshot)
        .filter(BuildingOperatingSnapshot.id.in_(snapshot_ids))
        .all()
        if snapshot_ids
        else []
    )
    snapshot_by_id = {int(item.id): item for item in snapshots}
    evidence_rows = (
        db.query(BuildingMetricFact, DataSource, ImportBatch, SourceRecord)
        .join(DataSource, DataSource.id == BuildingMetricFact.source_id)
        .outerjoin(ImportBatch, ImportBatch.id == BuildingMetricFact.batch_id)
        .outerjoin(SourceRecord, SourceRecord.id == BuildingMetricFact.source_record_id)
        .filter(
            BuildingMetricFact.snapshot_id.in_(snapshot_ids),
            BuildingMetricFact.metric_definition_id == definition.id,
        )
        .all()
        if snapshot_ids
        else []
    )
    evidence_by_snapshot = {
        int(fact.snapshot_id): (fact, source, batch, record)
        for fact, source, batch, record in evidence_rows
    }
    actor_ids = {
        int(actor_id)
        for snapshot in snapshots
        for actor_id in (snapshot.submitted_by, snapshot.reviewed_by, snapshot.published_by)
        if actor_id is not None
    }
    actor_ids.update(
        int(actor_id)
        for _fact, _source, batch, _record in evidence_rows
        if batch is not None
        for actor_id in (batch.actor_id, batch.reviewed_by)
        if actor_id is not None
    )
    actors = db.query(User).filter(User.id.in_(tuple(actor_ids))).all() if actor_ids else []
    actor_by_id = {int(actor.id): actor for actor in actors}

    items: list[dict] = []
    for contribution in contributions:
        building = contribution.get("building") or {}
        building_id = int(building.get("id"))
        operating_gate = contribution.get("operating_gate") or contribution.get("gate") or {}
        contribution_enabled = bool(contribution.get("contribution_enabled", True))
        contribution_metric = (contribution.get("metrics") or {}).get(normalized_code)
        aggregation_enabled = (
            bool((contribution_metric or {}).get("aggregation_eligible", contribution_enabled))
            if is_portfolio_scope
            else True
        )
        if contribution.get("publication_status") == "live_adapter":
            live_metric = contribution_metric
            source = next(
                (
                    item
                    for item in displayed.get("sources") or []
                    if item.get("building_id") in {None, building_id}
                    and item.get("source_type") == "detail"
                ),
                None,
            )
            gate_reason = (
                (live_metric or {}).get("aggregation_excluded_reason")
                or (contribution.get("reason") if not aggregation_enabled else None)
            )
            reason = gate_reason or (live_metric or {}).get("unavailable_reason") or (
                "当前明细适配器尚未提供该指标" if live_metric is None else None
            )
            coverage = (live_metric or {}).get("coverage") or {}
            included = bool(
                live_metric is not None
                and aggregation_enabled
                and reason is None
                and int(coverage.get("included_buildings") or 0) == 1
            )
            live_reason = reason or (
                "当天明细按 OPS_V1 实时适配"
                if is_portfolio_scope
                else "当前单楼指标可用；板块合计开关不适用于单楼范围"
            )
            items.append(
                {
                    "building": building,
                    "status": "live_detail_limited",
                    "included": included,
                    "reason": live_reason,
                    "operating_gate": operating_gate,
                    "data_classification": "simulated",
                    "snapshot": None,
                    "fact": (
                        _live_fact_payload(live_metric, unit=definition.unit)
                        if live_metric is not None
                        else None
                    ),
                    "source": (
                        {
                            "id": source.get("id"),
                            "source_type": source.get("source_type", "detail"),
                            "name": source.get("name", "DEMO_A 现有租赁明细库"),
                            "owner": "",
                            "cadence": "live",
                            "freshness_rule": "未建立统一业务观测时间规则",
                            "is_active": True,
                            "data_classification": "simulated",
                        }
                        if source is not None
                        else None
                    ),
                    "batch": None,
                    "record": None,
                    "limitations": [
                        "现有明细库未记录统一业务观测时间，不能用计算时间替代数据更新时间。",
                        "实时明细适配没有导入批次和来源行，当前只能追溯计算口径与分子分母。",
                        *(
                            ["所选指标尚未接入当前明细适配器，因此不生成数值或计算版本。"]
                            if live_metric is None
                            else []
                        ),
                    ],
                }
            )
            continue

        snapshot_id = contribution.get("snapshot_id")
        snapshot = snapshot_by_id.get(int(snapshot_id)) if snapshot_id is not None else None
        evidence = evidence_by_snapshot.get(int(snapshot_id)) if snapshot_id is not None else None
        if snapshot is None or evidence is None or contribution_metric is None:
            reason = contribution.get("reason") or (
                "该统计日快照未包含所选指标" if snapshot is not None else "该统计日没有当前已发布快照"
            )
            items.append(
                {
                    "building": building,
                    "status": "missing",
                    "included": False,
                    "reason": reason,
                    "operating_gate": operating_gate,
                    "data_classification": "simulated",
                    "snapshot": (
                        _snapshot_payload(snapshot, actor_by_id=actor_by_id)
                        if snapshot is not None
                        else None
                    ),
                    "fact": None,
                    "source": None,
                    "batch": None,
                    "record": None,
                    "limitations": [],
                }
            )
            continue

        fact, source, batch, record = evidence
        gate_reason = contribution_metric.get("aggregation_excluded_reason") or (
            contribution.get("reason") if not aggregation_enabled else None
        )
        reason = gate_reason or contribution_metric.get("unavailable_reason")
        coverage = contribution_metric.get("coverage") or {}
        included = bool(
            aggregation_enabled
            and reason is None
            and int(coverage.get("included_buildings") or 0) == 1
        )
        # Keep the snapshot-evidence-v1 status vocabulary stable.  The reason
        # explains the single-building semantics, while clients may present a
        # more specific label from scope.scope_type.
        item_status = "included" if included else "excluded"
        item_reason = reason or (
            "当前单楼指标可用；板块合计开关不适用于单楼范围"
            if not is_portfolio_scope
            else "已纳入当前同日合计"
        )
        items.append(
            {
                "building": building,
                "status": item_status,
                "included": included,
                "reason": item_reason,
                "operating_gate": operating_gate,
                "data_classification": "simulated",
                "snapshot": _snapshot_payload(snapshot, actor_by_id=actor_by_id),
                "fact": _fact_payload(
                    fact,
                    definition=definition,
                    contribution_metric=contribution_metric,
                ),
                "source": _source_payload(source),
                "batch": _batch_payload(batch, actor_by_id=actor_by_id),
                "record": _record_payload(record),
                "limitations": [],
            }
        )

    limitations = [
        "本系统当前从明细到板块合计均为模拟数据，仅用于功能演示和口径校验，不代表真实经营情况。",
        "本页展示系统已发布的归一化指标事实及其追溯元数据，不代表原始 Excel 文件已由系统保存或验真。",
        "源文件名、文件校验值和解析器版本是提交时登记信息；来源行校验值基于系统接收的归一化指标数据。",
    ]
    if normalized_code.startswith(("area.", "occupancy.")):
        limitations.append(
            "当前模拟面积指标尚未直接关联面积测量版本；若未来接入实测数据，仍须补齐面积口径、边界、测量日期和证据。"
        )

    return {
        "contract_version": EVIDENCE_CONTRACT_VERSION,
        "definition_version": "OPS_V1",
        "data_classification": displayed.get("data_classification", "simulated"),
        "gate_policy_version": displayed.get("gate_policy_version"),
        "rollout": displayed.get("rollout") or {},
        "as_of_date": effective_date.isoformat(),
        "scope": displayed.get("scope") or {},
        "metric": _definition_payload(definition, aggregate),
        "available_metrics": _available_metrics(
            definitions,
            displayed_metrics=displayed_metrics,
        ),
        "limitations": limitations,
        "items": items,
    }
