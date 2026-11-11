from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "20260508_0013"
down_revision = "20260508_0012"
branch_labels = None
depends_on = None


def _index_names(inspector, table_name: str) -> set[str]:
    if not inspector.has_table(table_name):
        return set()
    return {item["name"] for item in inspector.get_indexes(table_name) if item.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("contract_deposits"):
        op.create_table(
            "contract_deposits",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id"), nullable=False),
            sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
            sa.Column("deposit_type", sa.String(length=30), nullable=True, server_default="租赁押金"),
            sa.Column("expected_amount", sa.Float(), nullable=True, server_default="0"),
            sa.Column("received_amount", sa.Float(), nullable=True, server_default="0"),
            sa.Column("refunded_amount", sa.Float(), nullable=True, server_default="0"),
            sa.Column("offset_amount", sa.Float(), nullable=True, server_default="0"),
            sa.Column("status", sa.String(length=20), nullable=True, server_default="unpaid"),
            sa.Column("due_date", sa.Date(), nullable=True),
            sa.Column("received_date", sa.Date(), nullable=True),
            sa.Column("refund_date", sa.Date(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    inspector = inspect(bind)
    indexes = _index_names(inspector, "contract_deposits")
    for name, columns in {
        "ix_contract_deposits_contract_id": ["contract_id"],
        "ix_contract_deposits_customer_id": ["customer_id"],
        "ix_contract_deposits_deposit_type": ["deposit_type"],
        "ix_contract_deposits_status": ["status"],
    }.items():
        if name not in indexes:
            op.create_index(name, "contract_deposits", columns, unique=False)

    if inspector.has_table("contracts"):
        bind.execute(
            text(
                """
                INSERT INTO contract_deposits
                (
                    contract_id,
                    customer_id,
                    deposit_type,
                    expected_amount,
                    received_amount,
                    refunded_amount,
                    offset_amount,
                    status,
                    notes,
                    created_at,
                    updated_at
                )
                SELECT
                    contracts.id,
                    contracts.customer_id,
                    '租赁押金',
                    COALESCE(contracts.deposit, 0),
                    CASE
                        WHEN COALESCE(contracts.deposit_status, '') IN ('', '已收')
                        THEN COALESCE(contracts.deposit, 0)
                        ELSE 0
                    END,
                    0,
                    0,
                    CASE
                        WHEN COALESCE(contracts.deposit_status, '') IN ('', '已收')
                        THEN 'paid'
                        ELSE 'unpaid'
                    END,
                    '从合同押金字段迁移',
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                FROM contracts
                WHERE COALESCE(contracts.deposit, 0) > 0
                  AND NOT EXISTS (
                    SELECT 1
                    FROM contract_deposits
                    WHERE contract_deposits.contract_id = contracts.id
                  )
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("contract_deposits"):
        op.drop_table("contract_deposits")
