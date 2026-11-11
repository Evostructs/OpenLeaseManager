"""Add isolated portfolio overview grants for leadership summary access.

Revision ID: 20260805_0027
Revises: 20260805_0026
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260805_0027"
down_revision = "20260805_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_portfolio_overview_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column(
            "include_future_buildings",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("granted_by", sa.Integer(), nullable=True),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_user_portfolio_overview_valid_range",
        ),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "portfolio_id",
            "valid_from",
            name="uq_user_portfolio_overview_from",
        ),
    )
    op.create_index(
        op.f("ix_user_portfolio_overview_grants_id"),
        "user_portfolio_overview_grants",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_portfolio_overview_grants_portfolio_id"),
        "user_portfolio_overview_grants",
        ["portfolio_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_portfolio_overview_grants_user_id"),
        "user_portfolio_overview_grants",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    count = int(
        bind.execute(
            sa.text("SELECT COUNT(*) FROM user_portfolio_overview_grants")
        ).scalar_one()
        or 0
    )
    if count:
        raise RuntimeError(
            "阶段3E降级已停止：仍有板块概况授权，降级会丢失权限数据；"
            f"overview_grants={count}。"
        )
    op.drop_index(
        op.f("ix_user_portfolio_overview_grants_user_id"),
        table_name="user_portfolio_overview_grants",
    )
    op.drop_index(
        op.f("ix_user_portfolio_overview_grants_portfolio_id"),
        table_name="user_portfolio_overview_grants",
    )
    op.drop_index(
        op.f("ix_user_portfolio_overview_grants_id"),
        table_name="user_portfolio_overview_grants",
    )
    op.drop_table("user_portfolio_overview_grants")
