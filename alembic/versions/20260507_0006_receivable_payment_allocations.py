from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "20260507_0006"
down_revision = "20260507_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("receivable_payments"):
        op.create_table(
            "receivable_payments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id"), nullable=False),
            sa.Column("period_month", sa.String(length=7), nullable=False),
            sa.Column("payment_date", sa.Date(), nullable=True),
            sa.Column("amount", sa.Float(), server_default="0"),
            sa.Column("allocation_type", sa.String(length=20), server_default="收款"),
            sa.Column("payment_method", sa.String(length=20), server_default=""),
            sa.Column("reference_no", sa.String(length=100), server_default=""),
            sa.Column("notes", sa.Text(), server_default=""),
            sa.Column("operator", sa.String(length=50), server_default=""),
            sa.Column("source", sa.String(length=20), server_default="manual"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_receivable_payments_contract_id", "receivable_payments", ["contract_id"])
        op.create_index("ix_receivable_payments_period_month", "receivable_payments", ["period_month"])

    if inspector.has_table("receivable_records"):
        op.execute(
            text(
                """
                INSERT INTO receivable_payments (
                    contract_id,
                    period_month,
                    payment_date,
                    amount,
                    allocation_type,
                    payment_method,
                    reference_no,
                    notes,
                    operator,
                    source
                )
                SELECT
                    receivable_records.contract_id,
                    receivable_records.period_month,
                    receivable_records.received_date,
                    receivable_records.received_amount,
                    '收款',
                    '',
                    '',
                    receivable_records.notes,
                    receivable_records.operator,
                    'legacy_summary'
                FROM receivable_records
                WHERE COALESCE(receivable_records.received_amount, 0) > 0
                  AND NOT EXISTS (
                    SELECT 1
                    FROM receivable_payments
                    WHERE receivable_payments.contract_id = receivable_records.contract_id
                      AND receivable_payments.period_month = receivable_records.period_month
                      AND receivable_payments.source = 'legacy_summary'
                  )
                """
            )
        )


def downgrade() -> None:
    pass
