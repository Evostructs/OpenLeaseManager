"""付款日字段、月度出账固化表、存量孤儿房源引用清理

Revision ID: 20260610_0017
Revises: 20260610_0016
Create Date: 2026-06-10
"""
import sqlalchemy as sa
from alembic import op


revision = "20260610_0017"
down_revision = "20260610_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 合同付款日（每月 X 日到期；空 = 月末）
    op.add_column("contracts", sa.Column("payment_due_day", sa.Integer(), nullable=True))

    # 2) 月度出账固化表：固化后的月份金额以本表为准，合同条款变更不再影响
    op.create_table(
        "receivable_statements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id"), nullable=False, index=True),
        sa.Column("period_month", sa.String(length=7), nullable=False, index=True),
        sa.Column("rent", sa.Float(), nullable=False, server_default="0"),
        sa.Column("property_fee", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="auto"),
        sa.Column("operator", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("contract_id", "period_month", name="uq_statement_contract_month"),
    )

    # 3) 存量孤儿引用清理：房源明细指向已不存在的房间时置空（保留 room_no 文本）
    op.execute("UPDATE contract_rooms SET room_id = NULL WHERE room_id IS NOT NULL AND room_id NOT IN (SELECT id FROM rooms)")


def downgrade() -> None:
    op.drop_table("receivable_statements")
    op.drop_column("contracts", "payment_due_day")
