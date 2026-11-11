"""Seed the confirmed five-building catalog without inventing operating data.

Revision ID: 20260805_0025
Revises: 20260805_0024
Create Date: 2026-08-05
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op


revision = "20260805_0025"
down_revision = "20260805_0024"
branch_labels = None
depends_on = None


PORTFOLIO_CODE = "DEMO_OFFICE_PORTFOLIO"
PORTFOLIO_NAME = "示例资产组合（写字楼，暂定）"
DEMO_A_CODE = "DEMO_A"
DEMO_A_PROVISIONAL_NAME = "示例双塔中心（暂定）"
DEMO_A_FORMAL_NAME = "示例双塔中心"
DEMO_A_EFFECTIVE_DATE = "2026-08-04"
NEW_BUILDING_EFFECTIVE_DATE = "2026-08-05"
ORIGINAL_SEEDED_AT = "2026-08-04 21:00:00"

FORMAL_BUILDINGS = (
    (DEMO_A_CODE, DEMO_A_FORMAL_NAME, "detail"),
    ("DEMO_B", "示例主裙楼中心", "snapshot"),
    ("DEMO_C", "示例单栋中心", "snapshot"),
    ("DEMO_D", "示例办公中心", "snapshot"),
    ("DEMO_E", "示例商务中心", "snapshot"),
)
NEW_BUILDINGS = FORMAL_BUILDINGS[1:]

# Every direct reference that could make removal of a newly seeded building
# destructive. Indirect facts are protected through snapshots and sources.
NEW_BUILDING_REFERENCE_TABLES = (
    "zones",
    "contracts",
    "rooms",
    "contract_rooms",
    "customer_follow_ups",
    "attachments",
    "operation_logs",
    "customer_building_profiles",
    "user_scope_grants",
    "data_sources",
    "area_measurements",
    "building_operating_snapshots",
)


def _scalar(bind, statement: str, parameters: dict | None = None) -> int:
    return int(bind.execute(sa.text(statement), parameters or {}).scalar_one())


def _migration_time() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None, microsecond=0)


def _foreign_key_violations(bind) -> int:
    if bind.dialect.name != "sqlite":
        return 0
    return len(bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall())


def _assert_no_formal_identity_conflicts(bind) -> None:
    code_parameters = {f"code_{index}": code.casefold() for index, (code, _name, _mode) in enumerate(FORMAL_BUILDINGS)}
    code_placeholders = ", ".join(f":code_{index}" for index in range(len(FORMAL_BUILDINGS)))
    code_rows = bind.execute(
        sa.text(
            f"""
            SELECT code, name
            FROM buildings
            WHERE lower(trim(code)) IN ({code_placeholders})
            """
        ),
        code_parameters,
    ).mappings().all()
    code_conflicts = [
        str(row["code"])
        for row in code_rows
        if str(row["code"]).strip().casefold() != DEMO_A_CODE.casefold()
    ]
    if code_conflicts:
        raise RuntimeError(
            "正式楼宇代码冲突：待写入代码已被现有楼宇占用；迁移未执行任何写入。 "
            + ", ".join(sorted(code_conflicts, key=str.casefold))
        )

    name_parameters = {f"name_{index}": name for index, (_code, name, _mode) in enumerate(FORMAL_BUILDINGS)}
    name_placeholders = ", ".join(f":name_{index}" for index in range(len(FORMAL_BUILDINGS)))
    name_rows = bind.execute(
        sa.text(
            f"""
            SELECT code, name
            FROM buildings
            WHERE trim(name) IN ({name_placeholders})
            """
        ),
        name_parameters,
    ).mappings().all()
    if name_rows:
        summary = ", ".join(
            f"{row['code']}={row['name']}"
            for row in sorted(name_rows, key=lambda item: str(item["code"]).casefold())
        )
        raise RuntimeError(
            "正式楼宇名称冲突：确认名称已被现有楼宇使用；迁移未执行任何写入。 "
            + summary
        )


def _assert_upgrade_baseline(bind) -> tuple[int, int]:
    _assert_no_formal_identity_conflicts(bind)
    blockers: dict[str, int] = {}

    portfolio_id = bind.execute(
        sa.text("SELECT id FROM portfolios WHERE code = :code"),
        {"code": PORTFOLIO_CODE},
    ).scalar_one_or_none()
    demo_a_id = bind.execute(
        sa.text("SELECT id FROM buildings WHERE code = :code"),
        {"code": DEMO_A_CODE},
    ).scalar_one_or_none()

    if _scalar(bind, "SELECT COUNT(*) FROM portfolios") != 1:
        blockers["portfolios"] = _scalar(bind, "SELECT COUNT(*) FROM portfolios")
    if _scalar(bind, "SELECT COUNT(*) FROM buildings") != 1:
        blockers["buildings"] = _scalar(bind, "SELECT COUNT(*) FROM buildings")
    if _scalar(bind, "SELECT COUNT(*) FROM portfolio_buildings") != 1:
        blockers["portfolio_buildings"] = _scalar(bind, "SELECT COUNT(*) FROM portfolio_buildings")

    if portfolio_id is None:
        blockers["seed_portfolio_missing"] = 1
    else:
        portfolio_match = _scalar(
            bind,
            """
            SELECT COUNT(*) FROM portfolios
            WHERE id = :portfolio_id
              AND code = :code
              AND name = :name
              AND is_provisional = 1
              AND is_active = 1
              AND created_at = :seeded_at
              AND updated_at = :seeded_at
            """,
            {
                "portfolio_id": int(portfolio_id),
                "code": PORTFOLIO_CODE,
                "name": PORTFOLIO_NAME,
                "seeded_at": ORIGINAL_SEEDED_AT,
            },
        )
        if portfolio_match != 1:
            blockers["seed_portfolio_changed"] = 1

    if demo_a_id is None:
        blockers["seed_demo_a_missing"] = 1
    else:
        demo_a_match = _scalar(
            bind,
            """
            SELECT COUNT(*) FROM buildings
            WHERE id = :building_id
              AND code = :code
              AND name = :name
              AND timezone = 'Asia/Shanghai'
              AND currency = 'CNY'
              AND ingestion_mode = 'detail'
              AND is_provisional = 1
              AND is_active = 1
              AND created_at = :seeded_at
              AND updated_at = :seeded_at
            """,
            {
                "building_id": int(demo_a_id),
                "code": DEMO_A_CODE,
                "name": DEMO_A_PROVISIONAL_NAME,
                "seeded_at": ORIGINAL_SEEDED_AT,
            },
        )
        if demo_a_match != 1:
            blockers["seed_demo_a_changed"] = 1

    if portfolio_id is not None and demo_a_id is not None:
        membership_match = _scalar(
            bind,
            """
            SELECT COUNT(*) FROM portfolio_buildings
            WHERE portfolio_id = :portfolio_id
              AND building_id = :building_id
              AND valid_from = :valid_from
              AND valid_to IS NULL
              AND created_at = :seeded_at
            """,
            {
                "portfolio_id": int(portfolio_id),
                "building_id": int(demo_a_id),
                "valid_from": DEMO_A_EFFECTIVE_DATE,
                "seeded_at": ORIGINAL_SEEDED_AT,
            },
        )
        if membership_match != 1:
            blockers["seed_demo_a_membership_changed"] = 1

    foreign_key_violations = _foreign_key_violations(bind)
    if foreign_key_violations:
        blockers["foreign_key_violations"] = foreign_key_violations

    if blockers:
        summary = ", ".join(f"{key}={value}" for key, value in sorted(blockers.items()))
        raise RuntimeError(
            "正式楼宇迁移基线已变化：仅接受 0024 的单一 DEMO_A 种子基线；"
            "迁移未执行任何写入。 "
            + summary
        )

    return int(portfolio_id), int(demo_a_id)


def _assert_upgraded_state(bind, *, portfolio_id: int, migrated_at: datetime, grants_before: int) -> None:
    blockers: dict[str, int] = {}
    if _scalar(bind, "SELECT COUNT(*) FROM portfolios") != 1:
        blockers["portfolios"] = _scalar(bind, "SELECT COUNT(*) FROM portfolios")
    if _scalar(bind, "SELECT COUNT(*) FROM buildings") != len(FORMAL_BUILDINGS):
        blockers["buildings"] = _scalar(bind, "SELECT COUNT(*) FROM buildings")
    if _scalar(bind, "SELECT COUNT(*) FROM portfolio_buildings") != len(FORMAL_BUILDINGS):
        blockers["portfolio_buildings"] = _scalar(bind, "SELECT COUNT(*) FROM portfolio_buildings")
    grants_after = _scalar(bind, "SELECT COUNT(*) FROM user_scope_grants")
    if grants_after != grants_before:
        blockers["user_scope_grants_changed"] = abs(grants_after - grants_before) or 1

    for code, name, ingestion_mode in FORMAL_BUILDINGS:
        if code == DEMO_A_CODE:
            building_match = _scalar(
                bind,
                """
                SELECT COUNT(*) FROM buildings
                WHERE code = :code AND name = :name
                  AND timezone = 'Asia/Shanghai' AND currency = 'CNY'
                  AND ingestion_mode = :ingestion_mode
                  AND is_provisional = 0 AND is_active = 1
                  AND created_at = :original_seeded_at
                  AND updated_at = :migrated_at
                """,
                {
                    "code": code,
                    "name": name,
                    "ingestion_mode": ingestion_mode,
                    "original_seeded_at": ORIGINAL_SEEDED_AT,
                    "migrated_at": migrated_at,
                },
            )
            valid_from = DEMO_A_EFFECTIVE_DATE
            membership_created_at: datetime | str = ORIGINAL_SEEDED_AT
        else:
            building_match = _scalar(
                bind,
                """
                SELECT COUNT(*) FROM buildings
                WHERE code = :code AND name = :name
                  AND timezone = 'Asia/Shanghai' AND currency = 'CNY'
                  AND ingestion_mode = :ingestion_mode
                  AND is_provisional = 0 AND is_active = 1
                  AND created_at = :migrated_at
                  AND updated_at = :migrated_at
                """,
                {
                    "code": code,
                    "name": name,
                    "ingestion_mode": ingestion_mode,
                    "migrated_at": migrated_at,
                },
            )
            valid_from = NEW_BUILDING_EFFECTIVE_DATE
            membership_created_at = migrated_at
        if building_match != 1:
            blockers[f"building:{code}"] = 1
            continue
        building_id = int(
            bind.execute(
                sa.text("SELECT id FROM buildings WHERE code = :code"),
                {"code": code},
            ).scalar_one()
        )
        membership_match = _scalar(
            bind,
            """
            SELECT COUNT(*) FROM portfolio_buildings
            WHERE portfolio_id = :portfolio_id
              AND building_id = :building_id
              AND valid_from = :valid_from
              AND valid_to IS NULL
              AND created_at = :created_at
            """,
            {
                "portfolio_id": portfolio_id,
                "building_id": building_id,
                "valid_from": valid_from,
                "created_at": membership_created_at,
            },
        )
        if membership_match != 1:
            blockers[f"membership:{code}"] = 1

    foreign_key_violations = _foreign_key_violations(bind)
    if foreign_key_violations:
        blockers["foreign_key_violations"] = foreign_key_violations
    if blockers:
        summary = ", ".join(f"{key}={value}" for key, value in sorted(blockers.items()))
        raise RuntimeError("正式楼宇迁移写后校验失败：" + summary)


def upgrade() -> None:
    bind = op.get_bind()
    portfolio_id, demo_a_id = _assert_upgrade_baseline(bind)
    grants_before = _scalar(bind, "SELECT COUNT(*) FROM user_scope_grants")
    migrated_at = _migration_time()

    updated = bind.execute(
        sa.text(
            """
            UPDATE buildings
            SET name = :name, is_provisional = 0, updated_at = :updated_at
            WHERE id = :building_id
            """
        ),
        {
            "name": DEMO_A_FORMAL_NAME,
            "updated_at": migrated_at,
            "building_id": demo_a_id,
        },
    )
    if updated.rowcount != 1:
        raise RuntimeError("正式楼宇迁移写入失败：DEMO_A 未被唯一更新")

    for code, name, ingestion_mode in NEW_BUILDINGS:
        bind.execute(
            sa.text(
                """
                INSERT INTO buildings (
                    code, name, timezone, currency, ingestion_mode,
                    is_provisional, is_active, created_at, updated_at
                ) VALUES (
                    :code, :name, 'Asia/Shanghai', 'CNY', :ingestion_mode,
                    0, 1, :created_at, :updated_at
                )
                """
            ),
            {
                "code": code,
                "name": name,
                "ingestion_mode": ingestion_mode,
                "created_at": migrated_at,
                "updated_at": migrated_at,
            },
        )
        building_id = int(
            bind.execute(
                sa.text("SELECT id FROM buildings WHERE code = :code"),
                {"code": code},
            ).scalar_one()
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO portfolio_buildings (
                    portfolio_id, building_id, valid_from, valid_to, created_at
                ) VALUES (
                    :portfolio_id, :building_id, :valid_from, NULL, :created_at
                )
                """
            ),
            {
                "portfolio_id": portfolio_id,
                "building_id": building_id,
                "valid_from": NEW_BUILDING_EFFECTIVE_DATE,
                "created_at": migrated_at,
            },
        )

    _assert_upgraded_state(
        bind,
        portfolio_id=portfolio_id,
        migrated_at=migrated_at,
        grants_before=grants_before,
    )


def _assert_downgrade_safe(bind) -> tuple[int, dict[str, int]]:
    blockers: dict[str, int] = {}
    portfolio_id = bind.execute(
        sa.text("SELECT id FROM portfolios WHERE code = :code"),
        {"code": PORTFOLIO_CODE},
    ).scalar_one_or_none()
    if portfolio_id is None:
        blockers["portfolio_missing"] = 1

    building_ids: dict[str, int] = {}
    building_rows: dict[str, dict] = {}
    for code, _name, _mode in FORMAL_BUILDINGS:
        rows = bind.execute(
            sa.text("SELECT * FROM buildings WHERE code = :code"),
            {"code": code},
        ).mappings().all()
        if len(rows) != 1:
            blockers[f"building:{code}:count"] = len(rows)
            continue
        building_rows[code] = dict(rows[0])
        building_ids[code] = int(rows[0]["id"])

    migrated_at = building_rows.get("DEMO_B", {}).get("created_at")
    if migrated_at is None:
        blockers["seed_timestamp_missing"] = 1

    for code, name, ingestion_mode in FORMAL_BUILDINGS:
        row = building_rows.get(code)
        if row is None:
            continue
        expected_created_at = ORIGINAL_SEEDED_AT if code == DEMO_A_CODE else migrated_at
        expected_updated_at = migrated_at
        expected = {
            "name": name,
            "timezone": "Asia/Shanghai",
            "currency": "CNY",
            "ingestion_mode": ingestion_mode,
            "is_provisional": 0,
            "is_active": 1,
            "created_at": expected_created_at,
            "updated_at": expected_updated_at,
        }
        if any(row.get(key) != value for key, value in expected.items()):
            blockers[f"building:{code}:changed"] = 1

    if portfolio_id is not None:
        for code, _name, _mode in FORMAL_BUILDINGS:
            building_id = building_ids.get(code)
            if building_id is None:
                continue
            valid_from = DEMO_A_EFFECTIVE_DATE if code == DEMO_A_CODE else NEW_BUILDING_EFFECTIVE_DATE
            created_at: object = ORIGINAL_SEEDED_AT if code == DEMO_A_CODE else migrated_at
            membership_match = _scalar(
                bind,
                """
                SELECT COUNT(*) FROM portfolio_buildings
                WHERE portfolio_id = :portfolio_id
                  AND building_id = :building_id
                  AND valid_from = :valid_from
                  AND valid_to IS NULL
                  AND created_at = :created_at
                """,
                {
                    "portfolio_id": int(portfolio_id),
                    "building_id": building_id,
                    "valid_from": valid_from,
                    "created_at": created_at,
                },
            )
            if membership_match != 1:
                blockers[f"membership:{code}:changed"] = 1

    for code, _name, _mode in NEW_BUILDINGS:
        building_id = building_ids.get(code)
        if building_id is None:
            continue
        membership_count = _scalar(
            bind,
            "SELECT COUNT(*) FROM portfolio_buildings WHERE building_id = :building_id",
            {"building_id": building_id},
        )
        if membership_count != 1:
            blockers[f"portfolio_buildings:{code}"] = membership_count
        for table_name in NEW_BUILDING_REFERENCE_TABLES:
            count = _scalar(
                bind,
                f"SELECT COUNT(*) FROM {table_name} WHERE building_id = :building_id",
                {"building_id": building_id},
            )
            if count:
                blockers[f"{table_name}:{code}"] = count

    foreign_key_violations = _foreign_key_violations(bind)
    if foreign_key_violations:
        blockers["foreign_key_violations"] = foreign_key_violations

    if blockers:
        summary = ", ".join(f"{key}={value}" for key, value in sorted(blockers.items()))
        raise RuntimeError(
            "正式楼宇安全降级已停止：楼宇身份已变化或已有业务引用；"
            "迁移未执行任何写入。 "
            + summary
        )

    return int(portfolio_id), building_ids


def downgrade() -> None:
    bind = op.get_bind()
    portfolio_id, building_ids = _assert_downgrade_safe(bind)

    for code, _name, _mode in NEW_BUILDINGS:
        building_id = building_ids[code]
        deleted_membership = bind.execute(
            sa.text(
                """
                DELETE FROM portfolio_buildings
                WHERE portfolio_id = :portfolio_id AND building_id = :building_id
                """
            ),
            {"portfolio_id": portfolio_id, "building_id": building_id},
        )
        if deleted_membership.rowcount != 1:
            raise RuntimeError(f"正式楼宇降级写入失败：{code} 成员关系未被唯一移除")
        deleted_building = bind.execute(
            sa.text("DELETE FROM buildings WHERE id = :building_id AND code = :code"),
            {"building_id": building_id, "code": code},
        )
        if deleted_building.rowcount != 1:
            raise RuntimeError(f"正式楼宇降级写入失败：{code} 未被唯一移除")

    restored = bind.execute(
        sa.text(
            """
            UPDATE buildings
            SET name = :name, is_provisional = 1, updated_at = :updated_at
            WHERE id = :building_id AND code = :code
            """
        ),
        {
            "name": DEMO_A_PROVISIONAL_NAME,
            "updated_at": ORIGINAL_SEEDED_AT,
            "building_id": building_ids[DEMO_A_CODE],
            "code": DEMO_A_CODE,
        },
    )
    if restored.rowcount != 1:
        raise RuntimeError("正式楼宇降级写入失败：DEMO_A 未被唯一恢复")

    if _scalar(
        bind,
        "SELECT COUNT(*) FROM buildings WHERE code IN ('DEMO_B', 'DEMO_C', 'DEMO_D', 'DEMO_E')",
    ):
        raise RuntimeError("正式楼宇降级写后校验失败：仍存在新增楼宇")
    demo_a_restored = _scalar(
        bind,
        """
        SELECT COUNT(*) FROM buildings
        WHERE id = :building_id AND code = 'DEMO_A'
          AND name = :name AND is_provisional = 1
          AND created_at = :seeded_at AND updated_at = :seeded_at
        """,
        {
            "building_id": building_ids[DEMO_A_CODE],
            "name": DEMO_A_PROVISIONAL_NAME,
            "seeded_at": ORIGINAL_SEEDED_AT,
        },
    )
    if demo_a_restored != 1 or _foreign_key_violations(bind):
        raise RuntimeError("正式楼宇降级写后校验失败：DEMO_A 或外键状态异常")
