"""授权修订并发控制、不可变历史和稳定代码唯一性。

Revision ID: 20260805_0022
Revises: 20260805_0021
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.time import now_in_timezone


revision = "20260805_0022"
down_revision = "20260805_0021"
branch_labels = None
depends_on = None


NORMALIZED_CODE_INDEXES = (
    ("buildings", "ux_buildings_code_normalized"),
    ("portfolios", "ux_portfolios_code_normalized"),
)


def _assert_normalized_codes_are_unique(bind) -> None:
    conflicts: list[str] = []
    for table_name, _index_name in NORMALIZED_CODE_INDEXES:
        rows = bind.execute(
            sa.text(
                f"""
                SELECT lower(trim(code)) AS normalized_code, COUNT(*) AS duplicate_count
                FROM {table_name}
                GROUP BY lower(trim(code))
                HAVING COUNT(*) > 1
                ORDER BY lower(trim(code))
                """
            )
        ).fetchall()
        conflicts.extend(
            f"{table_name}:{str(row[0])}={int(row[1])}"
            for row in rows
        )
    if conflicts:
        raise RuntimeError(
            "授权安全迁移已停止：楼宇或板块稳定代码存在忽略大小写/空格后的冲突；"
            "请先人工确认并修复。 " + ", ".join(conflicts)
        )


def _assert_history_is_empty(bind) -> None:
    count = int(
        bind.execute(sa.text("SELECT COUNT(*) FROM user_authorization_revisions")).scalar_one()
    )
    if count:
        raise RuntimeError(
            "授权安全降级已停止：不可变授权修订历史不为空；"
            "请使用已验证的整包恢复流程，不能静默丢弃历史。 "
            f"user_authorization_revisions={count}"
        )


def upgrade() -> None:
    bind = op.get_bind()
    migrated_at = now_in_timezone()
    _assert_normalized_codes_are_unique(bind)

    op.add_column(
        "users",
        sa.Column(
            "authorization_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    op.create_table(
        "authorization_control",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("global_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("global_revision >= 0", name="ck_authorization_control_revision"),
        sa.CheckConstraint("id = 1", name="ck_authorization_control_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO authorization_control (id, global_revision, updated_at)
            VALUES (1, 0, :updated_at)
            """
        ),
        {"updated_at": migrated_at},
    )

    op.create_table(
        "user_authorization_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target_user_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("global_revision", sa.Integer(), nullable=False),
        sa.Column("operator_user_id", sa.Integer(), nullable=False),
        sa.Column("operator_username", sa.String(length=50), nullable=False),
        sa.Column("before_state", sa.Text(), nullable=False),
        sa.Column("after_state", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("global_revision > 0", name="ck_user_authorization_global_revision_positive"),
        sa.CheckConstraint("revision > 0", name="ck_user_authorization_revision_positive"),
        sa.ForeignKeyConstraint(["operator_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("global_revision", name="uq_user_authorization_global_revision"),
        sa.UniqueConstraint("target_user_id", "revision", name="uq_user_authorization_revision"),
    )
    op.create_index(
        "ix_user_authorization_revisions_created_at",
        "user_authorization_revisions",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_user_authorization_revisions_id",
        "user_authorization_revisions",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_user_authorization_revisions_operator_user_id",
        "user_authorization_revisions",
        ["operator_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_authorization_revisions_target_user_id",
        "user_authorization_revisions",
        ["target_user_id"],
        unique=False,
    )

    for table_name, index_name in NORMALIZED_CODE_INDEXES:
        op.create_index(
            index_name,
            table_name,
            [sa.text("lower(trim(code))")],
            unique=True,
        )

    if bind.dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_user_authorization_revisions_no_update
            BEFORE UPDATE ON user_authorization_revisions
            BEGIN
                SELECT RAISE(ABORT, 'authorization revision history is immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_user_authorization_revisions_no_delete
            BEFORE DELETE ON user_authorization_revisions
            BEGIN
                SELECT RAISE(ABORT, 'authorization revision history is immutable');
            END
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    _assert_history_is_empty(bind)

    if bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_user_authorization_revisions_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_user_authorization_revisions_no_update")

    for table_name, index_name in reversed(NORMALIZED_CODE_INDEXES):
        op.drop_index(index_name, table_name=table_name)

    op.drop_index(
        "ix_user_authorization_revisions_target_user_id",
        table_name="user_authorization_revisions",
    )
    op.drop_index(
        "ix_user_authorization_revisions_operator_user_id",
        table_name="user_authorization_revisions",
    )
    op.drop_index(
        "ix_user_authorization_revisions_id",
        table_name="user_authorization_revisions",
    )
    op.drop_index(
        "ix_user_authorization_revisions_created_at",
        table_name="user_authorization_revisions",
    )
    op.drop_table("user_authorization_revisions")
    op.drop_table("authorization_control")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("authorization_revision")
