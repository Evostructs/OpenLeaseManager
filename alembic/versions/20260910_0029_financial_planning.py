"""Add project expenses and immutable financial planning versions.

Revision ID: 20260910_0029
Revises: 20260806_0028
Create Date: 2026-09-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260910_0029"
down_revision = "20260806_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financial_expenses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("building_id", sa.Integer(), sa.ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("category", sa.String(10), nullable=False),
        sa.Column("amount_yuan", sa.Numeric(18, 2), nullable=False),
        sa.Column("occurrence_month", sa.String(7), nullable=False),
        sa.Column("payment_month", sa.String(7), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=True),
        sa.Column("owner", sa.String(100), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("category IN ('opex', 'capex')", name="ck_financial_expenses_category"),
        sa.CheckConstraint("amount_yuan >= 0", name="ck_financial_expenses_amount"),
        sa.CheckConstraint("revision >= 1", name="ck_financial_expenses_revision"),
    )
    for field in ("building_id", "occurrence_month", "payment_month"):
        op.create_index(f"ix_financial_expenses_{field}", "financial_expenses", [field])
    op.create_table(
        "financial_plan_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("building_id", sa.Integer(), sa.ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("base_version_id", sa.Integer(), sa.ForeignKey("financial_plan_versions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("closed_through_month", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("client_state_json", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("building_id", "year", "revision", name="uq_financial_plan_version_revision"),
        sa.CheckConstraint("kind IN ('budget', 'forecast', 'scenario')", name="ck_financial_plan_versions_kind"),
        sa.CheckConstraint("closed_through_month BETWEEN 0 AND 12", name="ck_financial_plan_versions_closed_month"),
        sa.CheckConstraint("revision >= 1", name="ck_financial_plan_versions_revision"),
    )
    for field in ("building_id", "year"):
        op.create_index(f"ix_financial_plan_versions_{field}", "financial_plan_versions", [field])
    op.create_table(
        "financial_planning_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("building_id", sa.Integer(), sa.ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_budget_id", sa.Integer(), sa.ForeignKey("financial_plan_versions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("active_forecast_id", sa.Integer(), sa.ForeignKey("financial_plan_versions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("building_id", "year", name="uq_financial_planning_state_year"),
        sa.CheckConstraint("revision >= 0", name="ck_financial_planning_states_revision"),
    )
    op.create_index("ix_financial_planning_states_building_id", "financial_planning_states", ["building_id"])


def downgrade() -> None:
    # Removing populated planning tables would discard business history.
    bind = op.get_bind()
    for table in ("financial_planning_states", "financial_plan_versions", "financial_expenses"):
        if bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar():
            raise RuntimeError("财务计划表已有数据，请使用兼容0029的旧应用回退镜像；拒绝删除业务历史")
    op.drop_table("financial_planning_states")
    op.drop_table("financial_plan_versions")
    op.drop_table("financial_expenses")
