"""Durable receipts for idempotent receivable payments.

Revision ID: 20260928_0030
Revises: 20260910_0029
"""

import sqlalchemy as sa
from alembic import op

revision = "20260928_0030"
down_revision = "20260910_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "receivable_payment_operations",
        sa.Column("operation_id", sa.String(36), primary_key=True),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("receivable_payments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for field in ("contract_id", "payment_id"):
        op.create_index(f"ix_receivable_payment_operations_{field}", "receivable_payment_operations", [field])


def downgrade() -> None:
    # Dropping receipts would allow delayed retries to create duplicate payments.
    # Roll back application code while retaining this additive schema instead.
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM receivable_payment_operations")).scalar_one():
        raise RuntimeError("收款操作回执不可自动删除；回退应用时请保留此表。")
    op.drop_table("receivable_payment_operations")
