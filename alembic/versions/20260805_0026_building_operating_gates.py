"""Add append-only building presentation and aggregation gate history.

Revision ID: 20260805_0026
Revises: 20260805_0025
Create Date: 2026-08-05
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op


revision = "20260805_0026"
down_revision = "20260805_0025"
branch_labels = None
depends_on = None


SEED_DECISIONS = (
    ("DEMO_A", True, True, "保留示例双塔中心现有展示和合计行为"),
    ("DEMO_B", True, False, "模拟经营数据观察期，暂不计入板块合计"),
    ("DEMO_C", True, False, "模拟经营数据观察期，暂不计入板块合计"),
    ("DEMO_D", True, False, "模拟经营数据观察期，暂不计入板块合计"),
    ("DEMO_E", True, False, "模拟经营数据观察期，暂不计入板块合计"),
)

UPDATE_TRIGGER = "trg_building_operating_gate_events_no_update"
DELETE_TRIGGER = "trg_building_operating_gate_events_no_delete"


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None, microsecond=0)


def _create_immutable_guards(bind) -> None:
    if bind.dialect.name != "sqlite":
        return
    bind.exec_driver_sql(
        f"""
        CREATE TRIGGER {UPDATE_TRIGGER}
        BEFORE UPDATE ON building_operating_gate_events
        BEGIN
            SELECT RAISE(ABORT, 'building operating gate history is immutable');
        END
        """
    )
    bind.exec_driver_sql(
        f"""
        CREATE TRIGGER {DELETE_TRIGGER}
        BEFORE DELETE ON building_operating_gate_events
        BEGIN
            SELECT RAISE(ABORT, 'building operating gate history is immutable');
        END
        """
    )


def _assert_seed_buildings(bind) -> dict[str, int]:
    rows = bind.execute(
        sa.text(
            """
            SELECT id, code
            FROM buildings
            WHERE code IN ('DEMO_A', 'DEMO_B', 'DEMO_C', 'DEMO_D', 'DEMO_E')
              AND is_active = 1
            """
        )
    ).mappings().all()
    by_code = {str(row["code"]): int(row["id"]) for row in rows}
    expected = {item[0] for item in SEED_DECISIONS}
    if set(by_code) != expected:
        missing = ", ".join(sorted(expected - set(by_code))) or "none"
        raise RuntimeError(
            "building operating gate migration requires the confirmed five-building catalog; "
            f"missing={missing}"
        )
    return by_code


def upgrade() -> None:
    bind = op.get_bind()
    building_ids = _assert_seed_buildings(bind)
    op.create_table(
        "building_operating_gate_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("building_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("presentation_enabled", sa.Boolean(), nullable=False),
        sa.Column("contribution_enabled", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("previous_event_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_building_operating_gate_revision_positive"),
        sa.CheckConstraint(
            "presentation_enabled IN (0, 1)",
            name="ck_building_operating_gate_presentation_boolean",
        ),
        sa.CheckConstraint(
            "contribution_enabled IN (0, 1)",
            name="ck_building_operating_gate_contribution_boolean",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_building_operating_gate_reason_nonempty",
        ),
        sa.CheckConstraint(
            "origin IN ('migration', 'user')",
            name="ck_building_operating_gate_origin",
        ),
        sa.CheckConstraint(
            "(origin = 'migration' AND actor_id IS NULL) OR "
            "(origin = 'user' AND actor_id IS NOT NULL)",
            name="ck_building_operating_gate_actor_origin",
        ),
        sa.CheckConstraint(
            "(revision = 1 AND previous_event_id IS NULL) OR "
            "(revision > 1 AND previous_event_id IS NOT NULL)",
            name="ck_building_operating_gate_previous_revision",
        ),
        sa.ForeignKeyConstraint(["building_id"], ["buildings.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["building_id", "previous_event_id"],
            ["building_operating_gate_events.building_id", "building_operating_gate_events.id"],
            name="fk_building_operating_gate_previous_same_building",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("building_id", "revision", name="uq_building_operating_gate_revision"),
        sa.UniqueConstraint("building_id", "id", name="uq_building_operating_gate_building_id"),
    )
    op.create_index(
        "ix_building_operating_gate_events_building_id",
        "building_operating_gate_events",
        ["building_id"],
    )
    op.create_index(
        "ix_building_operating_gate_events_actor_id",
        "building_operating_gate_events",
        ["actor_id"],
    )
    op.create_index(
        "ix_building_operating_gate_events_previous_event_id",
        "building_operating_gate_events",
        ["previous_event_id"],
    )
    op.create_index(
        "ix_building_operating_gate_events_created_at",
        "building_operating_gate_events",
        ["created_at"],
    )

    seeded_at = _now()
    for code, presentation_enabled, contribution_enabled, reason in SEED_DECISIONS:
        bind.execute(
            sa.text(
                """
                INSERT INTO building_operating_gate_events (
                    building_id, revision, presentation_enabled, contribution_enabled,
                    reason, actor_id, origin, previous_event_id, created_at
                ) VALUES (
                    :building_id, 1, :presentation_enabled, :contribution_enabled,
                    :reason, NULL, 'migration', NULL, :created_at
                )
                """
            ),
            {
                "building_id": building_ids[code],
                "presentation_enabled": presentation_enabled,
                "contribution_enabled": contribution_enabled,
                "reason": reason,
                "created_at": seeded_at,
            },
        )
    _create_immutable_guards(bind)


def _assert_downgrade_safe(bind) -> None:
    rows = bind.execute(
        sa.text(
            """
            SELECT b.code, g.revision, g.presentation_enabled, g.contribution_enabled,
                   g.reason, g.actor_id, g.origin, g.previous_event_id
            FROM building_operating_gate_events g
            JOIN buildings b ON b.id = g.building_id
            ORDER BY b.code, g.revision
            """
        )
    ).mappings().all()
    expected = {
        code: (bool(presentation), bool(contribution), reason)
        for code, presentation, contribution, reason in SEED_DECISIONS
    }
    if len(rows) != len(expected):
        raise RuntimeError(
            "building operating gate downgrade refused: gate history contains non-seed decisions"
        )
    for row in rows:
        code = str(row["code"])
        wanted = expected.get(code)
        actual = (
            bool(row["presentation_enabled"]),
            bool(row["contribution_enabled"]),
            str(row["reason"]),
        )
        if (
            wanted is None
            or int(row["revision"]) != 1
            or actual != wanted
            or row["actor_id"] is not None
            or str(row["origin"]) != "migration"
            or row["previous_event_id"] is not None
        ):
            raise RuntimeError(
                "building operating gate downgrade refused: seed decision history changed"
            )


def downgrade() -> None:
    bind = op.get_bind()
    _assert_downgrade_safe(bind)
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {UPDATE_TRIGGER}")
        bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {DELETE_TRIGGER}")
    op.drop_index("ix_building_operating_gate_events_created_at", table_name="building_operating_gate_events")
    op.drop_index("ix_building_operating_gate_events_previous_event_id", table_name="building_operating_gate_events")
    op.drop_index("ix_building_operating_gate_events_actor_id", table_name="building_operating_gate_events")
    op.drop_index("ix_building_operating_gate_events_building_id", table_name="building_operating_gate_events")
    op.drop_table("building_operating_gate_events")
