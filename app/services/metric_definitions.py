from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.time import now_in_timezone
from app.models import MetricDefinition, User
from app.ops_v1 import OPS_V1_DEFINITIONS, definition_hash


def ensure_ops_v1_definitions(
    db: Session,
    *,
    publisher: User,
) -> tuple[MetricDefinition, ...]:
    """Install the frozen OPS_V1 catalog for a newly initialized database.

    Existing rows must match byte-for-byte at the semantic-field level.  This
    function never updates a published definition in place.
    """
    if publisher.id is None:
        raise ValueError("publisher must be persisted")
    published_at = now_in_timezone()
    rows: list[MetricDefinition] = []
    for item in OPS_V1_DEFINITIONS:
        expected_hash = definition_hash(item)
        existing = (
            db.query(MetricDefinition)
            .filter(
                MetricDefinition.code == item["code"],
                MetricDefinition.version == item["version"],
            )
            .one_or_none()
        )
        if existing is not None:
            if (
                existing.definition_hash != expected_hash
                or existing.publication_status != "published"
            ):
                raise HTTPException(409, f"指标定义 {item['code']} v1 与 OPS_V1 固化版本冲突")
            rows.append(existing)
            continue
        row = MetricDefinition(
            code=item["code"],
            name=item["name"],
            business_definition=item["business_definition"],
            version=int(item["version"]),
            valid_from=date.fromisoformat(item["valid_from"]),
            valid_to=None,
            grain=item["grain"],
            unit=item["unit"],
            currency=item["currency"],
            numerator_rule=item["numerator_rule"],
            denominator_rule=item["denominator_rule"],
            inclusion_rule=item["inclusion_rule"],
            exclusion_rule=item["exclusion_rule"],
            aggregation_rule=item["aggregation_rule"],
            coverage_rule=item["coverage_rule"],
            estimation_policy=item["estimation_policy"],
            freshness_policy=item["freshness_policy"],
            owner=item["owner"],
            definition_hash=expected_hash,
            publication_status="published",
            published_by=int(publisher.id),
            published_at=published_at,
            created_at=published_at,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return tuple(rows)


def published_ops_v1_by_code(db: Session) -> dict[str, MetricDefinition]:
    expected = {item["code"]: definition_hash(item) for item in OPS_V1_DEFINITIONS}
    rows = (
        db.query(MetricDefinition)
        .filter(
            MetricDefinition.version == 1,
            MetricDefinition.code.in_(tuple(expected)),
            MetricDefinition.publication_status == "published",
        )
        .all()
    )
    result = {
        row.code: row
        for row in rows
        if row.definition_hash == expected.get(row.code)
    }
    if len(result) != len(expected):
        missing = sorted(set(expected) - set(result))
        raise HTTPException(409, f"OPS_V1 指标定义不完整或已发生冲突：{','.join(missing)}")
    return result
