"""核心业务楼宇关系强约束

Revision ID: 20260805_0023
Revises: 20260805_0022
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260805_0023"
down_revision = "20260805_0022"
branch_labels = None
depends_on = None


BACKFILL_TABLE = "migration_0023_follow_up_backfills"


def _count(bind, sql: str) -> int:
    return int(bind.execute(sa.text(sql)).scalar_one() or 0)


def _database_check_counts(bind) -> tuple[int, int]:
    integrity_rows = bind.exec_driver_sql("PRAGMA integrity_check").fetchall()
    integrity_errors = sum(1 for row in integrity_rows if str(row[0]).lower() != "ok")
    foreign_key_errors = len(bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall())
    return integrity_errors, foreign_key_errors


def _upgrade_anomaly_counts(bind) -> tuple[dict[str, int], int, int]:
    standalone_follow_ups = _count(
        bind,
        """
        SELECT COUNT(*)
        FROM customer_follow_ups
        WHERE contract_id IS NULL AND building_id IS NULL
        """,
    )
    demo_a_rows = bind.execute(
        sa.text("SELECT id FROM buildings WHERE code = 'DEMO_A' ORDER BY id")
    ).fetchall()
    demo_a_building_id = int(demo_a_rows[0][0]) if len(demo_a_rows) == 1 else 0

    integrity_errors, foreign_key_errors = _database_check_counts(bind)
    counts = {
        "unexpected_backfill_marker_table": _count(
            bind,
            f"""
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type = 'table' AND name = '{BACKFILL_TABLE}'
            """,
        ),
        "database_integrity_errors": integrity_errors,
        "existing_foreign_key_errors": foreign_key_errors,
        "zones_null_building": _count(
            bind,
            "SELECT COUNT(*) FROM zones WHERE building_id IS NULL",
        ),
        "zones_unknown_building": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM zones z
            LEFT JOIN buildings b ON b.id = z.building_id
            WHERE b.id IS NULL
            """,
        ),
        "contracts_null_building": _count(
            bind,
            "SELECT COUNT(*) FROM contracts WHERE building_id IS NULL",
        ),
        "contracts_unknown_building": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM contracts c
            LEFT JOIN buildings b ON b.id = c.building_id
            WHERE c.building_id IS NOT NULL AND b.id IS NULL
            """,
        ),
        "rooms_null_building": _count(
            bind,
            "SELECT COUNT(*) FROM rooms WHERE building_id IS NULL",
        ),
        "rooms_null_zone": _count(
            bind,
            "SELECT COUNT(*) FROM rooms WHERE zone_id IS NULL",
        ),
        "rooms_unknown_building": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM rooms r
            LEFT JOIN buildings b ON b.id = r.building_id
            WHERE r.building_id IS NOT NULL AND b.id IS NULL
            """,
        ),
        "rooms_invalid_room_no": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM rooms
            WHERE room_no IS NULL
               OR length(trim(room_no)) = 0
               OR room_no <> trim(room_no)
            """,
        ),
        "rooms_duplicate_per_building": _count(
            bind,
            """
            SELECT COALESCE(SUM(duplicate_count - 1), 0)
            FROM (
                SELECT COUNT(*) AS duplicate_count
                FROM rooms
                GROUP BY building_id, room_no
                HAVING COUNT(*) > 1
            ) duplicates
            """,
        ),
        "rooms_missing_zone": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM rooms r
            LEFT JOIN zones z ON z.id = r.zone_id
            WHERE r.zone_id IS NOT NULL AND z.id IS NULL
            """,
        ),
        "rooms_cross_building_zone": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM rooms r
            JOIN zones z ON z.id = r.zone_id
            WHERE r.building_id IS NOT NULL AND z.building_id <> r.building_id
            """,
        ),
        "contract_rooms_null_building": _count(
            bind,
            "SELECT COUNT(*) FROM contract_rooms WHERE building_id IS NULL",
        ),
        "contract_rooms_null_room": _count(
            bind,
            "SELECT COUNT(*) FROM contract_rooms WHERE room_id IS NULL",
        ),
        "contract_rooms_unknown_building": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM contract_rooms cr
            LEFT JOIN buildings b ON b.id = cr.building_id
            WHERE cr.building_id IS NOT NULL AND b.id IS NULL
            """,
        ),
        "contract_rooms_invalid_room_no": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM contract_rooms
            WHERE room_no IS NULL
               OR length(trim(room_no)) = 0
               OR room_no <> trim(room_no)
            """,
        ),
        "contract_rooms_missing_contract": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM contract_rooms cr
            LEFT JOIN contracts c ON c.id = cr.contract_id
            WHERE c.id IS NULL
            """,
        ),
        "contract_rooms_cross_building_contract": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM contract_rooms cr
            JOIN contracts c ON c.id = cr.contract_id
            WHERE cr.building_id IS NOT NULL AND c.building_id <> cr.building_id
            """,
        ),
        "contract_rooms_missing_room": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM contract_rooms cr
            LEFT JOIN rooms r ON r.id = cr.room_id
            WHERE cr.room_id IS NOT NULL AND r.id IS NULL
            """,
        ),
        "contract_rooms_cross_building_room": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM contract_rooms cr
            JOIN rooms r ON r.id = cr.room_id
            WHERE cr.building_id IS NOT NULL AND r.building_id <> cr.building_id
            """,
        ),
        "contract_rooms_room_no_mismatch": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM contract_rooms cr
            JOIN rooms r ON r.id = cr.room_id
            WHERE cr.room_no IS NOT NULL AND r.room_no <> cr.room_no
            """,
        ),
        "follow_ups_null_building_with_contract": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM customer_follow_ups
            WHERE contract_id IS NOT NULL AND building_id IS NULL
            """,
        ),
        "follow_ups_unknown_building": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM customer_follow_ups f
            LEFT JOIN buildings b ON b.id = f.building_id
            WHERE f.building_id IS NOT NULL AND b.id IS NULL
            """,
        ),
        "follow_ups_missing_contract": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM customer_follow_ups f
            LEFT JOIN contracts c ON c.id = f.contract_id
            WHERE f.contract_id IS NOT NULL AND c.id IS NULL
            """,
        ),
        "follow_ups_cross_building_contract": _count(
            bind,
            """
            SELECT COUNT(*)
            FROM customer_follow_ups f
            JOIN contracts c ON c.id = f.contract_id
            WHERE f.building_id IS NOT NULL AND f.building_id <> c.building_id
            """,
        ),
        "standalone_follow_ups_without_unique_demo_a": (
            standalone_follow_ups if standalone_follow_ups and len(demo_a_rows) != 1 else 0
        ),
    }
    return counts, standalone_follow_ups, demo_a_building_id


def _assert_upgrade_is_safe(bind) -> tuple[int, int]:
    counts, standalone_follow_ups, demo_a_building_id = _upgrade_anomaly_counts(bind)
    if any(counts.values()):
        summary = ", ".join(f"{key}={value}" for key, value in counts.items())
        raise RuntimeError(
            "阶段2严格结构迁移已停止：只读预检发现不一致，尚未执行任何结构变更。 "
            + summary
        )
    return standalone_follow_ups, demo_a_building_id


def _assert_downgrade_is_safe(bind) -> None:
    duplicate_room_numbers = _count(
        bind,
        """
        SELECT COALESCE(SUM(duplicate_count - 1), 0)
        FROM (
            SELECT COUNT(*) AS duplicate_count
            FROM rooms
            GROUP BY room_no
            HAVING COUNT(*) > 1
        ) duplicates
        """,
    )
    if duplicate_room_numbers:
        raise RuntimeError(
            "阶段2严格结构降级已停止：0022 的全库房号唯一规则无法无损容纳当前数据；"
            f"duplicate_room_no={duplicate_room_numbers}。"
        )


def _verify_database(bind, phase: str) -> None:
    integrity_errors, foreign_key_errors = _database_check_counts(bind)
    if integrity_errors or foreign_key_errors:
        raise RuntimeError(
            f"阶段2严格结构{phase}校验失败："
            f"database_integrity_errors={integrity_errors}, "
            f"foreign_key_errors={foreign_key_errors}"
        )


def upgrade() -> None:
    bind = op.get_bind()
    standalone_follow_ups, demo_a_building_id = _assert_upgrade_is_safe(bind)

    # 仅记录本次迁移补齐的独立跟进，以便无损降回 0022；不记录或改动其他业务行。
    op.create_table(
        BACKFILL_TABLE,
        sa.Column("follow_up_id", sa.Integer(), primary_key=True),
        sa.Column("building_id", sa.Integer(), nullable=False),
    )
    if standalone_follow_ups:
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {BACKFILL_TABLE} (follow_up_id, building_id)
                SELECT id, :building_id
                FROM customer_follow_ups
                WHERE contract_id IS NULL AND building_id IS NULL
                """
            ),
            {"building_id": demo_a_building_id},
        )
        bind.execute(
            sa.text(
                f"""
                UPDATE customer_follow_ups
                SET building_id = :building_id
                WHERE id IN (SELECT follow_up_id FROM {BACKFILL_TABLE})
                """
            ),
            {"building_id": demo_a_building_id},
        )

    with op.batch_alter_table("zones", recreate="always") as batch:
        batch.create_unique_constraint(
            "uq_zones_building_id_id",
            ["building_id", "id"],
        )

    with op.batch_alter_table("contracts", recreate="always") as batch:
        batch.alter_column(
            "building_id",
            existing_type=sa.Integer(),
            existing_nullable=True,
            nullable=False,
        )
        batch.create_unique_constraint(
            "uq_contracts_building_id_id",
            ["building_id", "id"],
        )

    with op.batch_alter_table("rooms", recreate="always") as batch:
        batch.drop_index("ix_rooms_room_no")
        batch.alter_column(
            "building_id",
            existing_type=sa.Integer(),
            existing_nullable=True,
            nullable=False,
        )
        batch.alter_column(
            "zone_id",
            existing_type=sa.Integer(),
            existing_nullable=True,
            nullable=False,
        )
        batch.alter_column(
            "room_no",
            existing_type=sa.String(length=50),
            existing_nullable=True,
            nullable=False,
        )
        batch.create_index("ix_rooms_room_no", ["room_no"], unique=False)
        batch.create_unique_constraint(
            "uq_rooms_building_room_no",
            ["building_id", "room_no"],
        )
        batch.create_unique_constraint(
            "uq_rooms_building_id_id_room_no",
            ["building_id", "id", "room_no"],
        )
        batch.create_foreign_key(
            "fk_rooms_building_zone",
            "zones",
            ["building_id", "zone_id"],
            ["building_id", "id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_rooms_room_no_canonical",
            "room_no = trim(room_no) AND length(room_no) > 0",
        )

    with op.batch_alter_table("contract_rooms", recreate="always") as batch:
        batch.alter_column(
            "building_id",
            existing_type=sa.Integer(),
            existing_nullable=True,
            nullable=False,
        )
        batch.alter_column(
            "room_id",
            existing_type=sa.Integer(),
            existing_nullable=True,
            nullable=False,
        )
        batch.alter_column(
            "room_no",
            existing_type=sa.String(length=50),
            existing_nullable=True,
            nullable=False,
        )
        batch.create_foreign_key(
            "fk_contract_rooms_building_contract",
            "contracts",
            ["building_id", "contract_id"],
            ["building_id", "id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_contract_rooms_building_room_number",
            "rooms",
            ["building_id", "room_id", "room_no"],
            ["building_id", "id", "room_no"],
            ondelete="RESTRICT",
            onupdate="CASCADE",
        )
        batch.create_check_constraint(
            "ck_contract_rooms_room_no_canonical",
            "room_no = trim(room_no) AND length(room_no) > 0",
        )

    with op.batch_alter_table("customer_follow_ups", recreate="always") as batch:
        batch.alter_column(
            "building_id",
            existing_type=sa.Integer(),
            existing_nullable=True,
            nullable=False,
        )
        batch.create_foreign_key(
            "fk_customer_follow_ups_building_contract",
            "contracts",
            ["building_id", "contract_id"],
            ["building_id", "id"],
            ondelete="RESTRICT",
        )

    _verify_database(bind, "升级后")


def downgrade() -> None:
    bind = op.get_bind()
    _assert_downgrade_is_safe(bind)

    with op.batch_alter_table("customer_follow_ups", recreate="always") as batch:
        batch.drop_constraint(
            "fk_customer_follow_ups_building_contract",
            type_="foreignkey",
        )
        batch.alter_column(
            "building_id",
            existing_type=sa.Integer(),
            existing_nullable=False,
            nullable=True,
        )

    # 只撤销本迁移自动补齐且此后未被改到其他楼宇的值。
    bind.execute(
        sa.text(
            f"""
            UPDATE customer_follow_ups
            SET building_id = NULL
            WHERE contract_id IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM {BACKFILL_TABLE} marker
                  WHERE marker.follow_up_id = customer_follow_ups.id
                    AND marker.building_id = customer_follow_ups.building_id
              )
            """
        )
    )

    with op.batch_alter_table("contract_rooms", recreate="always") as batch:
        batch.drop_constraint(
            "fk_contract_rooms_building_room_number",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "fk_contract_rooms_building_contract",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "ck_contract_rooms_room_no_canonical",
            type_="check",
        )
        batch.alter_column(
            "building_id",
            existing_type=sa.Integer(),
            existing_nullable=False,
            nullable=True,
        )
        batch.alter_column(
            "room_id",
            existing_type=sa.Integer(),
            existing_nullable=False,
            nullable=True,
        )
        batch.alter_column(
            "room_no",
            existing_type=sa.String(length=50),
            existing_nullable=False,
            nullable=True,
        )

    with op.batch_alter_table("rooms", recreate="always") as batch:
        batch.drop_constraint("fk_rooms_building_zone", type_="foreignkey")
        batch.drop_constraint("ck_rooms_room_no_canonical", type_="check")
        batch.drop_constraint("uq_rooms_building_id_id_room_no", type_="unique")
        batch.drop_constraint("uq_rooms_building_room_no", type_="unique")
        batch.drop_index("ix_rooms_room_no")
        batch.alter_column(
            "building_id",
            existing_type=sa.Integer(),
            existing_nullable=False,
            nullable=True,
        )
        batch.alter_column(
            "zone_id",
            existing_type=sa.Integer(),
            existing_nullable=False,
            nullable=True,
        )
        batch.alter_column(
            "room_no",
            existing_type=sa.String(length=50),
            existing_nullable=False,
            nullable=True,
        )
        batch.create_index("ix_rooms_room_no", ["room_no"], unique=True)

    with op.batch_alter_table("contracts", recreate="always") as batch:
        batch.drop_constraint("uq_contracts_building_id_id", type_="unique")
        batch.alter_column(
            "building_id",
            existing_type=sa.Integer(),
            existing_nullable=False,
            nullable=True,
        )

    with op.batch_alter_table("zones", recreate="always") as batch:
        batch.drop_constraint("uq_zones_building_id_id", type_="unique")

    op.drop_table(BACKFILL_TABLE)
    _verify_database(bind, "降级后")
