"""OPS_V1 published definitions and immutable publication history

Revision ID: 20260805_0024
Revises: 20260805_0023
Create Date: 2026-08-05
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from alembic import op

from app.core.time import now_in_timezone
from app.ops_v1 import OPS_V1_DEFINITIONS, definition_hash


revision = "20260805_0024"
down_revision = "20260805_0023"
branch_labels = None
depends_on = None


TRIGGERS = (
    "trg_metric_definitions_published_no_update",
    "trg_metric_definitions_published_no_delete",
    "trg_building_snapshots_published_guard_update",
    "trg_building_snapshots_published_no_delete",
    "trg_building_metric_facts_published_guard_update",
    "trg_building_metric_facts_published_no_delete",
)


def _seed_definitions(bind) -> None:
    publisher_id = bind.execute(
        sa.text(
            """
            SELECT id
            FROM users
            WHERE is_active = 1
            ORDER BY CASE WHEN role = 'admin' THEN 0 ELSE 1 END, id
            LIMIT 1
            """
        )
    ).scalar_one_or_none()
    published_at = now_in_timezone()
    for item in OPS_V1_DEFINITIONS:
        expected_hash = definition_hash(item)
        existing = bind.execute(
            sa.text(
                """
                SELECT definition_hash, publication_status
                FROM metric_definitions
                WHERE code = :code AND version = :version
                """
            ),
            {"code": item["code"], "version": item["version"]},
        ).first()
        if existing is not None:
            if existing[0] != expected_hash or existing[1] != "published":
                raise RuntimeError(
                    f"OPS_V1 migration refused conflicting definition {item['code']} v1"
                )
            continue
        # A brand-new, not-yet-initialized database has no accountable actor.
        # Registration installs the same frozen catalog with the first admin as
        # publisher. Existing installations are seeded directly here.
        if publisher_id is None:
            continue
        bind.execute(
            sa.text(
                """
                INSERT INTO metric_definitions (
                    code, name, business_definition, version, valid_from, valid_to,
                    grain, unit, currency, numerator_rule, denominator_rule,
                    inclusion_rule, exclusion_rule, aggregation_rule, coverage_rule,
                    estimation_policy, freshness_policy, owner, definition_hash,
                    publication_status, published_by, published_at, created_at
                ) VALUES (
                    :code, :name, :business_definition, :version, :valid_from, NULL,
                    :grain, :unit, :currency, :numerator_rule, :denominator_rule,
                    :inclusion_rule, :exclusion_rule, :aggregation_rule, :coverage_rule,
                    :estimation_policy, :freshness_policy, :owner, :definition_hash,
                    'published', :published_by, :published_at, :created_at
                )
                """
            ),
            {
                **item,
                "valid_from": date.fromisoformat(item["valid_from"]),
                "definition_hash": expected_hash,
                "published_by": int(publisher_id),
                "published_at": published_at,
                "created_at": published_at,
            },
        )


def _create_guards(bind) -> None:
    if bind.dialect.name != "sqlite":
        return
    bind.exec_driver_sql(
        """
        CREATE TRIGGER trg_metric_definitions_published_no_update
        BEFORE UPDATE ON metric_definitions
        WHEN OLD.publication_status = 'published'
        BEGIN
            SELECT RAISE(ABORT, 'published metric definitions are immutable');
        END
        """
    )
    bind.exec_driver_sql(
        """
        CREATE TRIGGER trg_metric_definitions_published_no_delete
        BEFORE DELETE ON metric_definitions
        WHEN OLD.publication_status = 'published'
        BEGIN
            SELECT RAISE(ABORT, 'published metric definitions are immutable');
        END
        """
    )
    bind.exec_driver_sql(
        """
        CREATE TRIGGER trg_building_snapshots_published_guard_update
        BEFORE UPDATE ON building_operating_snapshots
        WHEN OLD.publication_status = 'superseded'
          OR (
            OLD.publication_status = 'published'
            AND NOT (
                NEW.publication_status = 'superseded'
                AND NEW.is_current = 0
                AND NEW.id IS OLD.id
                AND NEW.building_id IS OLD.building_id
                AND NEW.period_start IS OLD.period_start
                AND NEW.period_end IS OLD.period_end
                AND NEW.as_of_date IS OLD.as_of_date
                AND NEW.revision IS OLD.revision
                AND NEW.validation_status IS OLD.validation_status
                AND NEW.submitted_by IS OLD.submitted_by
                AND NEW.submitted_at IS OLD.submitted_at
                AND NEW.reviewed_by IS OLD.reviewed_by
                AND NEW.reviewed_at IS OLD.reviewed_at
                AND NEW.published_by IS OLD.published_by
                AND NEW.published_at IS OLD.published_at
                AND NEW.supersedes_id IS OLD.supersedes_id
                AND NEW.created_at IS OLD.created_at
            )
          )
        BEGIN
            SELECT RAISE(ABORT, 'published operating snapshots are immutable');
        END
        """
    )
    bind.exec_driver_sql(
        """
        CREATE TRIGGER trg_building_snapshots_published_no_delete
        BEFORE DELETE ON building_operating_snapshots
        WHEN OLD.publication_status IN ('published', 'superseded')
        BEGIN
            SELECT RAISE(ABORT, 'published operating snapshots are immutable');
        END
        """
    )
    bind.exec_driver_sql(
        """
        CREATE TRIGGER trg_building_metric_facts_published_guard_update
        BEFORE UPDATE ON building_metric_facts
        WHEN OLD.publication_status = 'superseded'
          OR (
            OLD.publication_status = 'published'
            AND NOT (
                NEW.publication_status = 'superseded'
                AND NEW.id IS OLD.id
                AND NEW.snapshot_id IS OLD.snapshot_id
                AND NEW.metric_definition_id IS OLD.metric_definition_id
                AND NEW.value IS OLD.value
                AND NEW.numerator IS OLD.numerator
                AND NEW.denominator IS OLD.denominator
                AND NEW.source_id IS OLD.source_id
                AND NEW.batch_id IS OLD.batch_id
                AND NEW.source_record_id IS OLD.source_record_id
                AND NEW.eligible_buildings IS OLD.eligible_buildings
                AND NEW.included_buildings IS OLD.included_buildings
                AND NEW.covered_area_sqm IS OLD.covered_area_sqm
                AND NEW.eligible_area_sqm IS OLD.eligible_area_sqm
                AND NEW.covered_days IS OLD.covered_days
                AND NEW.eligible_days IS OLD.eligible_days
                AND NEW.estimate_share IS OLD.estimate_share
                AND NEW.estimate_method IS OLD.estimate_method
                AND NEW.data_depth IS OLD.data_depth
                AND NEW.completeness_status IS OLD.completeness_status
                AND NEW.freshness_status IS OLD.freshness_status
                AND NEW.validation_status IS OLD.validation_status
                AND NEW.calculation_version IS OLD.calculation_version
                AND NEW.created_at IS OLD.created_at
            )
          )
        BEGIN
            SELECT RAISE(ABORT, 'published metric facts are immutable');
        END
        """
    )
    bind.exec_driver_sql(
        """
        CREATE TRIGGER trg_building_metric_facts_published_no_delete
        BEFORE DELETE ON building_metric_facts
        WHEN OLD.publication_status IN ('published', 'superseded')
        BEGIN
            SELECT RAISE(ABORT, 'published metric facts are immutable');
        END
        """
    )


def upgrade() -> None:
    bind = op.get_bind()
    _seed_definitions(bind)
    _create_guards(bind)


def _assert_downgrade_safe(bind) -> None:
    published_snapshots = int(
        bind.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM building_operating_snapshots
                WHERE publication_status IN ('published', 'superseded')
                """
            )
        ).scalar_one()
    )
    published_facts = int(
        bind.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM building_metric_facts
                WHERE publication_status IN ('published', 'superseded')
                """
            )
        ).scalar_one()
    )
    if published_snapshots or published_facts:
        raise RuntimeError(
            "OPS_V1 downgrade refused before removing publication guards: "
            f"published_or_superseded_snapshots={published_snapshots}, "
            f"published_or_superseded_facts={published_facts}"
        )


def downgrade() -> None:
    bind = op.get_bind()
    # This check deliberately happens before any trigger is removed.  A failed
    # downgrade therefore leaves both the Alembic revision and the immutable
    # publication history protections intact.
    _assert_downgrade_safe(bind)
    if bind.dialect.name == "sqlite":
        for name in TRIGGERS:
            bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
    for item in OPS_V1_DEFINITIONS:
        bind.execute(
            sa.text(
                """
                DELETE FROM metric_definitions
                WHERE code = :code
                  AND version = :version
                  AND definition_hash = :definition_hash
                  AND NOT EXISTS (
                      SELECT 1 FROM building_metric_facts f
                      WHERE f.metric_definition_id = metric_definitions.id
                  )
                """
            ),
            {
                "code": item["code"],
                "version": item["version"],
                "definition_hash": definition_hash(item),
            },
        )
