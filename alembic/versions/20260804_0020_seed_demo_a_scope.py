"""
建立暂定 DEMO_A 范围，并在异常为零时回填现有关系。

Revision ID: 20260804_0020
Revises: 20260804_0019
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260804_0020"
down_revision = "20260804_0019"
branch_labels = None
depends_on = None


PORTFOLIO_CODE = "DEMO_OFFICE_PORTFOLIO"
BUILDING_CODE = "DEMO_A"
EFFECTIVE_DATE = "2026-08-04"
MIGRATED_AT = "2026-08-04 21:00:00"

# Synthetic fixture counts for migration regression tests only.
# Real legacy databases require a separately reviewed migration; do not infer compatibility.
EXPECTED_LEGACY_COUNTS = {
    "rooms": 24,
    "contracts": 12,
    "customers": 12,
    "contract_rooms": 12,
    "customer_follow_ups": 4,
    "attachments": 18,
    "operation_logs": 16,
    "users": 3,
}

ZONE_SEEDS = (
    ("SOUTH_OFFICE", "南塔", "office", 10),
    ("NORTH_OFFICE", "北塔", "office", 20),
    ("COMMERCIAL_SUPPORT", "商业配套", "commercial", 30),
)

DOWNGRADE_MUST_BE_EMPTY = (
    "area_measurements",
    "building_metric_facts",
    "building_operating_snapshots",
    "customer_building_profiles",
    "data_sources",
    "import_batches",
    "import_errors",
    "metric_definitions",
    "source_records",
    "user_capabilities",
)


def _scalar(bind, statement: str) -> int:
    return int(bind.execute(sa.text(statement)).scalar_one())


def _preflight_legacy_relationships(bind) -> None:
    """只输出聚合计数；任一异常都在回填前阻断迁移。"""

    legacy_counts = {
        table_name: _scalar(bind, f"SELECT COUNT(*) FROM {table_name}")
        for table_name in EXPECTED_LEGACY_COUNTS
    }
    if any(legacy_counts.values()) and legacy_counts != EXPECTED_LEGACY_COUNTS:
        summary = ", ".join(
            f"{table_name}={legacy_counts[table_name]}"
            f"/expected={EXPECTED_LEGACY_COUNTS[table_name]}"
            for table_name in EXPECTED_LEGACY_COUNTS
        )
        raise RuntimeError(f"DEMO_A 迁移样例基线不匹配：{summary}")

    counts = {
        "empty_room_number": _scalar(
            bind,
            "SELECT COUNT(*) FROM rooms WHERE room_no IS NULL OR TRIM(room_no) = ''",
        ),
        "empty_relation_room_number": _scalar(
            bind,
            """
            SELECT COUNT(*) FROM contract_rooms
            WHERE room_no IS NULL OR TRIM(room_no) = ''
            """,
        ),
        "no_match": _scalar(
            bind,
            """
            SELECT COUNT(*)
            FROM contract_rooms cr
            WHERE (SELECT COUNT(*) FROM rooms r WHERE r.room_no = cr.room_no) = 0
            """,
        ),
        "multiple_match": _scalar(
            bind,
            """
            SELECT COUNT(*)
            FROM contract_rooms cr
            WHERE (SELECT COUNT(*) FROM rooms r WHERE r.room_no = cr.room_no) > 1
            """,
        ),
        "tower_conflict": _scalar(
            bind,
            """
            SELECT COUNT(*)
            FROM contract_rooms cr
            JOIN rooms r ON r.room_no = cr.room_no
            WHERE COALESCE(TRIM(cr.tower), '') <> COALESCE(TRIM(r.tower), '')
            """,
        ),
        "area_conflict": _scalar(
            bind,
            """
            SELECT COUNT(*)
            FROM contract_rooms cr
            JOIN rooms r ON r.room_no = cr.room_no
            WHERE (cr.area IS NULL) <> (r.area IS NULL)
               OR (
                    cr.area IS NOT NULL
                    AND r.area IS NOT NULL
                    AND ABS(cr.area - r.area) > 0.01
               )
            """,
        ),
        "unknown_room_tower": _scalar(
            bind,
            """
            SELECT COUNT(*)
            FROM rooms
            WHERE COALESCE(TRIM(tower), '') NOT IN ('南塔', '北塔', '商业配套')
            """,
        ),
        "existing_room_link": _scalar(
            bind,
            "SELECT COUNT(*) FROM contract_rooms WHERE room_id IS NOT NULL",
        ),
    }
    counts["foreign_key_violation"] = len(
        bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    )

    if any(counts.values()):
        summary = ", ".join(f"{key}={value}" for key, value in counts.items())
        raise RuntimeError(f"DEMO_A 关系预检未通过：{summary}")


def _preflight_safe_downgrade(bind) -> None:
    """Stop before clearing seed/scope data if stage 1C has received any writes."""
    blockers: dict[str, int] = {}
    legacy_counts = {
        table_name: _scalar(bind, f"SELECT COUNT(*) FROM {table_name}")
        for table_name in EXPECTED_LEGACY_COUNTS
    }
    if any(legacy_counts.values()) and legacy_counts != EXPECTED_LEGACY_COUNTS:
        blockers.update(
            {
                f"legacy_count:{table_name}": count
                for table_name, count in legacy_counts.items()
                if count != EXPECTED_LEGACY_COUNTS[table_name]
            }
        )

    for table_name in DOWNGRADE_MUST_BE_EMPTY:
        count = _scalar(bind, f"SELECT COUNT(*) FROM {table_name}")
        if count:
            blockers[f"table:{table_name}"] = count

    portfolio_count = _scalar(bind, "SELECT COUNT(*) FROM portfolios")
    building_count = _scalar(bind, "SELECT COUNT(*) FROM buildings")
    zone_count = _scalar(bind, "SELECT COUNT(*) FROM zones")
    membership_count = _scalar(bind, "SELECT COUNT(*) FROM portfolio_buildings")
    if portfolio_count != 1:
        blockers["structure:portfolios"] = portfolio_count
    if building_count != 1:
        blockers["structure:buildings"] = building_count
    if zone_count != len(ZONE_SEEDS):
        blockers["structure:zones"] = zone_count
    if membership_count != 1:
        blockers["structure:portfolio_buildings"] = membership_count

    portfolio_id = bind.execute(
        sa.text("SELECT id FROM portfolios WHERE code = :code"),
        {"code": PORTFOLIO_CODE},
    ).scalar_one_or_none()
    building_id = bind.execute(
        sa.text("SELECT id FROM buildings WHERE code = :code"),
        {"code": BUILDING_CODE},
    ).scalar_one_or_none()
    if portfolio_id is None:
        blockers["structure:seed_portfolio"] = 0
    else:
        portfolio_mismatch = int(
            bind.execute(
                sa.text(
                    """
                    SELECT COUNT(*) FROM portfolios
                    WHERE id = :portfolio_id
                      AND (
                           name <> :name
                        OR is_provisional <> 1
                        OR is_active <> 1
                        OR created_at <> :seeded_at
                        OR updated_at <> :seeded_at
                      )
                    """
                ),
                {
                    "portfolio_id": portfolio_id,
                    "name": "示例资产组合（写字楼，暂定）",
                    "seeded_at": MIGRATED_AT,
                },
            ).scalar_one()
        )
        if portfolio_mismatch:
            blockers["structure:seed_portfolio_changed"] = portfolio_mismatch

    if building_id is None:
        blockers["structure:demo_a_building"] = 0
    else:
        building_mismatch = int(
            bind.execute(
                sa.text(
                    """
                    SELECT COUNT(*) FROM buildings
                    WHERE id = :building_id
                      AND (
                           name <> :name
                        OR timezone <> 'Asia/Shanghai'
                        OR currency <> 'CNY'
                        OR ingestion_mode <> 'detail'
                        OR is_provisional <> 1
                        OR is_active <> 1
                        OR created_at <> :seeded_at
                        OR updated_at <> :seeded_at
                      )
                    """
                ),
                {
                    "building_id": building_id,
                    "name": "示例双塔中心（暂定）",
                    "seeded_at": MIGRATED_AT,
                },
            ).scalar_one()
        )
        if building_mismatch:
            blockers["structure:seed_building_changed"] = building_mismatch

        if portfolio_id is not None:
            membership_mismatch = int(
                bind.execute(
                    sa.text(
                        """
                        SELECT COUNT(*) FROM portfolio_buildings
                        WHERE portfolio_id = :portfolio_id
                          AND building_id = :building_id
                          AND (
                               valid_from <> :valid_from
                            OR valid_to IS NOT NULL
                            OR created_at <> :seeded_at
                          )
                        """
                    ),
                    {
                        "portfolio_id": portfolio_id,
                        "building_id": building_id,
                        "valid_from": EFFECTIVE_DATE,
                        "seeded_at": MIGRATED_AT,
                    },
                ).scalar_one()
            )
            exact_membership_count = int(
                bind.execute(
                    sa.text(
                        """
                        SELECT COUNT(*) FROM portfolio_buildings
                        WHERE portfolio_id = :portfolio_id AND building_id = :building_id
                        """
                    ),
                    {"portfolio_id": portfolio_id, "building_id": building_id},
                ).scalar_one()
            )
            if membership_mismatch or exact_membership_count != 1:
                blockers["structure:seed_membership_changed"] = max(
                    membership_mismatch,
                    abs(exact_membership_count - 1),
                    1,
                )

        for code, name, zone_type, sort_order in ZONE_SEEDS:
            exact_zone_count = int(
                bind.execute(
                    sa.text(
                        """
                        SELECT COUNT(*) FROM zones
                        WHERE building_id = :building_id
                          AND code = :code
                          AND name = :name
                          AND zone_type = :zone_type
                          AND sort_order = :sort_order
                          AND valid_from = :valid_from
                          AND valid_to IS NULL
                          AND is_active = 1
                          AND created_at = :seeded_at
                          AND updated_at = :seeded_at
                        """
                    ),
                    {
                        "building_id": building_id,
                        "code": code,
                        "name": name,
                        "zone_type": zone_type,
                        "sort_order": sort_order,
                        "valid_from": EFFECTIVE_DATE,
                        "seeded_at": MIGRATED_AT,
                    },
                ).scalar_one()
            )
            if exact_zone_count != 1:
                blockers[f"structure:seed_zone_changed:{code}"] = abs(exact_zone_count - 1) or 1

        legacy_scope_mismatches = {
            "rooms": _scalar(
                bind,
                f"""
                SELECT COUNT(*)
                FROM rooms r
                LEFT JOIN zones z ON z.id = r.zone_id
                WHERE r.building_id IS NULL
                   OR r.building_id <> {int(building_id)}
                   OR r.zone_id IS NULL
                   OR z.building_id <> {int(building_id)}
                   OR z.code <> CASE TRIM(r.tower)
                        WHEN '南塔' THEN 'SOUTH_OFFICE'
                        WHEN '北塔' THEN 'NORTH_OFFICE'
                        WHEN '商业配套' THEN 'COMMERCIAL_SUPPORT'
                        ELSE '__INVALID__'
                      END
                """,
            ),
            "contracts": _scalar(
                bind,
                f"""
                SELECT COUNT(*) FROM contracts
                WHERE building_id IS NULL OR building_id <> {int(building_id)}
                """,
            ),
            "contract_rooms": _scalar(
                bind,
                f"""
                SELECT COUNT(*)
                FROM contract_rooms cr
                LEFT JOIN contracts c ON c.id = cr.contract_id
                LEFT JOIN rooms r ON r.id = cr.room_id
                WHERE cr.building_id IS NULL
                   OR cr.building_id <> {int(building_id)}
                   OR c.id IS NULL
                   OR c.building_id <> cr.building_id
                   OR cr.room_id IS NULL
                   OR r.id IS NULL
                   OR r.building_id <> cr.building_id
                   OR r.room_no <> cr.room_no
                   OR TRIM(r.tower) <> TRIM(cr.tower)
                """,
            ),
            "customer_follow_ups": _scalar(
                bind,
                f"""
                SELECT COUNT(*)
                FROM customer_follow_ups f
                LEFT JOIN contracts c ON c.id = f.contract_id
                WHERE (f.contract_id IS NULL AND f.building_id IS NOT NULL)
                   OR (
                        f.contract_id IS NOT NULL
                        AND (
                             c.id IS NULL
                          OR f.building_id IS NULL
                          OR f.building_id <> c.building_id
                          OR f.building_id <> {int(building_id)}
                        )
                   )
                """,
            ),
        }
        for table_name, mismatch_count in legacy_scope_mismatches.items():
            if mismatch_count:
                blockers[f"scope:{table_name}:changed"] = mismatch_count

        grant_count = _scalar(bind, "SELECT COUNT(*) FROM user_scope_grants")
        user_count = _scalar(bind, "SELECT COUNT(*) FROM users")
        if grant_count != user_count:
            blockers["table:user_scope_grants_count"] = abs(grant_count - user_count) or 1
        unexpected_grants = _scalar(
            bind,
            f"""
            SELECT COUNT(*)
            FROM user_scope_grants g
            LEFT JOIN users u ON u.id = g.user_id
            WHERE u.id IS NULL
               OR g.scope_type <> 'building'
               OR g.portfolio_id IS NOT NULL
               OR g.building_id <> {int(building_id)}
               OR g.access_level <> CASE
                    WHEN u.role IN ('admin', 'editor') THEN 'edit'
                    ELSE 'view'
                  END
               OR g.include_future_buildings <> 0
               OR g.valid_from <> '{EFFECTIVE_DATE}'
               OR g.valid_to IS NOT NULL
               OR g.granted_by IS NOT NULL
               OR g.granted_at <> '{MIGRATED_AT}'
            """,
        )
        if unexpected_grants:
            blockers["table:user_scope_grants_custom"] = unexpected_grants

    attachment_scopes = _scalar(
        bind,
        """
        SELECT COUNT(*) FROM attachments
        WHERE scope_type IS NOT NULL OR portfolio_id IS NOT NULL OR building_id IS NOT NULL
        """,
    )
    log_scopes = _scalar(
        bind,
        """
        SELECT COUNT(*) FROM operation_logs
        WHERE scope_type IS NOT NULL OR portfolio_id IS NOT NULL OR building_id IS NOT NULL
        """,
    )
    if attachment_scopes:
        blockers["scope:attachments"] = attachment_scopes
    if log_scopes:
        blockers["scope:operation_logs"] = log_scopes

    if blockers:
        summary = ", ".join(f"{key}={value}" for key, value in sorted(blockers.items()))
        raise RuntimeError(
            "DEMO_A 安全降级已停止：阶段 1C 已有新增或新增范围数据；"
            "必须使用经验证的整包恢复，或先显式处理这些数据。"
            f" {summary}"
        )


def _seed_scope(bind) -> tuple[int, int, dict[str, int]]:
    bind.execute(
        sa.text(
            """
            INSERT OR IGNORE INTO portfolios
                (code, name, is_provisional, is_active, created_at, updated_at)
            VALUES
                (:code, :name, 1, 1, :created_at, :updated_at)
            """
        ),
        {
            "code": PORTFOLIO_CODE,
            "name": "示例资产组合（写字楼，暂定）",
            "created_at": MIGRATED_AT,
            "updated_at": MIGRATED_AT,
        },
    )
    bind.execute(
        sa.text(
            """
            INSERT OR IGNORE INTO buildings
                (
                    code, name, timezone, currency, ingestion_mode,
                    is_provisional, is_active, created_at, updated_at
                )
            VALUES
                (
                    :code, :name, 'Asia/Shanghai', 'CNY', 'detail',
                    1, 1, :created_at, :updated_at
                )
            """
        ),
        {
            "code": BUILDING_CODE,
            "name": "示例双塔中心（暂定）",
            "created_at": MIGRATED_AT,
            "updated_at": MIGRATED_AT,
        },
    )

    portfolio_id = int(
        bind.execute(
            sa.text("SELECT id FROM portfolios WHERE code = :code"),
            {"code": PORTFOLIO_CODE},
        ).scalar_one()
    )
    building_id = int(
        bind.execute(
            sa.text("SELECT id FROM buildings WHERE code = :code"),
            {"code": BUILDING_CODE},
        ).scalar_one()
    )

    bind.execute(
        sa.text(
            """
            INSERT INTO portfolio_buildings
                (portfolio_id, building_id, valid_from, valid_to, created_at)
            SELECT :portfolio_id, :building_id, :valid_from, NULL, :created_at
            WHERE NOT EXISTS (
                SELECT 1 FROM portfolio_buildings
                WHERE portfolio_id = :portfolio_id
                  AND building_id = :building_id
                  AND valid_from = :valid_from
            )
            """
        ),
        {
            "portfolio_id": portfolio_id,
            "building_id": building_id,
            "valid_from": EFFECTIVE_DATE,
            "created_at": MIGRATED_AT,
        },
    )

    for code, name, zone_type, sort_order in ZONE_SEEDS:
        bind.execute(
            sa.text(
                """
                INSERT INTO zones
                    (
                        building_id, code, name, zone_type, sort_order,
                        valid_from, valid_to, is_active, created_at, updated_at
                    )
                SELECT
                    :building_id, :code, :name, :zone_type, :sort_order,
                    :valid_from, NULL, 1, :created_at, :updated_at
                WHERE NOT EXISTS (
                    SELECT 1 FROM zones
                    WHERE building_id = :building_id AND code = :code
                )
                """
            ),
            {
                "building_id": building_id,
                "code": code,
                "name": name,
                "zone_type": zone_type,
                "sort_order": sort_order,
                "valid_from": EFFECTIVE_DATE,
                "created_at": MIGRATED_AT,
                "updated_at": MIGRATED_AT,
            },
        )

    zone_ids = {
        str(row.code): int(row.id)
        for row in bind.execute(
            sa.text(
                "SELECT id, code FROM zones WHERE building_id = :building_id"
            ),
            {"building_id": building_id},
        )
    }
    return portfolio_id, building_id, zone_ids


def upgrade() -> None:
    bind = op.get_bind()
    _preflight_legacy_relationships(bind)
    _portfolio_id, building_id, zone_ids = _seed_scope(bind)

    bind.execute(
        sa.text("UPDATE contracts SET building_id = :building_id WHERE building_id IS NULL"),
        {"building_id": building_id},
    )
    bind.execute(
        sa.text("UPDATE rooms SET building_id = :building_id WHERE building_id IS NULL"),
        {"building_id": building_id},
    )
    for tower_name, zone_code in (
        ("南塔", "SOUTH_OFFICE"),
        ("北塔", "NORTH_OFFICE"),
        ("商业配套", "COMMERCIAL_SUPPORT"),
    ):
        bind.execute(
            sa.text(
                """
                UPDATE rooms
                SET zone_id = :zone_id
                WHERE building_id = :building_id
                  AND zone_id IS NULL
                  AND TRIM(tower) = :tower_name
                """
            ),
            {
                "zone_id": zone_ids[zone_code],
                "building_id": building_id,
                "tower_name": tower_name,
            },
        )

    bind.execute(
        sa.text(
            """
            UPDATE contract_rooms
            SET building_id = (
                SELECT c.building_id FROM contracts c
                WHERE c.id = contract_rooms.contract_id
            )
            WHERE building_id IS NULL
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE contract_rooms
            SET room_id = (
                SELECT MIN(r.id)
                FROM rooms r
                WHERE r.building_id = contract_rooms.building_id
                  AND r.room_no = contract_rooms.room_no
            )
            WHERE room_id IS NULL
              AND building_id IS NOT NULL
              AND 1 = (
                  SELECT COUNT(*)
                  FROM rooms r
                  WHERE r.building_id = contract_rooms.building_id
                    AND r.room_no = contract_rooms.room_no
              )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE customer_follow_ups
            SET building_id = (
                SELECT c.building_id FROM contracts c
                WHERE c.id = customer_follow_ups.contract_id
            )
            WHERE building_id IS NULL AND contract_id IS NOT NULL
            """
        )
    )

    bind.execute(
        sa.text(
            """
            INSERT INTO user_scope_grants
                (
                    user_id, scope_type, portfolio_id, building_id,
                    access_level, include_future_buildings,
                    valid_from, valid_to, granted_by, granted_at
                )
            SELECT
                u.id, 'building', NULL, :building_id,
                CASE WHEN u.role IN ('admin', 'editor') THEN 'edit' ELSE 'view' END,
                0, :valid_from, NULL, NULL, :granted_at
            FROM users u
            WHERE NOT EXISTS (
                SELECT 1 FROM user_scope_grants g
                WHERE g.user_id = u.id
                  AND g.building_id = :building_id
                  AND g.valid_from = :valid_from
            )
            """
        ),
        {
            "building_id": building_id,
            "valid_from": EFFECTIVE_DATE,
            "granted_at": MIGRATED_AT,
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    _preflight_safe_downgrade(bind)
    building_id = bind.execute(
        sa.text("SELECT id FROM buildings WHERE code = :code"),
        {"code": BUILDING_CODE},
    ).scalar_one_or_none()
    portfolio_id = bind.execute(
        sa.text("SELECT id FROM portfolios WHERE code = :code"),
        {"code": PORTFOLIO_CODE},
    ).scalar_one_or_none()

    if building_id is not None:
        bind.execute(
            sa.text(
                """
                DELETE FROM user_scope_grants
                WHERE building_id = :building_id
                  AND valid_from = :valid_from
                  AND granted_by IS NULL
                """
            ),
            {"building_id": building_id, "valid_from": EFFECTIVE_DATE},
        )
        bind.execute(
            sa.text(
                "UPDATE customer_follow_ups SET building_id = NULL WHERE building_id = :building_id"
            ),
            {"building_id": building_id},
        )
        bind.execute(
            sa.text(
                """
                UPDATE contract_rooms
                SET room_id = NULL, building_id = NULL
                WHERE building_id = :building_id
                """
            ),
            {"building_id": building_id},
        )
        bind.execute(
            sa.text(
                "UPDATE rooms SET zone_id = NULL, building_id = NULL WHERE building_id = :building_id"
            ),
            {"building_id": building_id},
        )
        bind.execute(
            sa.text("UPDATE contracts SET building_id = NULL WHERE building_id = :building_id"),
            {"building_id": building_id},
        )
        bind.execute(
            sa.text("DELETE FROM zones WHERE building_id = :building_id"),
            {"building_id": building_id},
        )
        bind.execute(
            sa.text("DELETE FROM portfolio_buildings WHERE building_id = :building_id"),
            {"building_id": building_id},
        )
        bind.execute(
            sa.text("DELETE FROM buildings WHERE id = :building_id"),
            {"building_id": building_id},
        )

    if portfolio_id is not None:
        bind.execute(
            sa.text("DELETE FROM portfolios WHERE id = :portfolio_id"),
            {"portfolio_id": portfolio_id},
        )
