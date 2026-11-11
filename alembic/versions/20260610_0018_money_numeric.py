"""金额列类型 Float -> NUMERIC(18,2)（批量重建表，数据不变）

Revision ID: 20260610_0018
Revises: 20260610_0017
Create Date: 2026-06-10
"""
import sqlalchemy as sa
from alembic import op


revision = "20260610_0018"
down_revision = "20260610_0017"
branch_labels = None
depends_on = None


MONEY_COLUMNS = {
    "contracts": ["unit_price", "monthly_rent", "broker_fee", "deposit", "property_fee_rate"],
    "contract_rooms": ["unit_price", "monthly_rent"],
    "rent_terms": ["unit_price", "monthly_amount", "adjustment_value"],
    "receivable_records": ["received_amount", "invoice_amount"],
    "receivable_payments": ["amount"],
    "contract_deposits": ["expected_amount", "received_amount", "refunded_amount", "offset_amount"],
    "receivable_statements": ["rent", "property_fee", "total_amount"],
}


def _alter(money_type, existing_type) -> None:
    for table, columns in MONEY_COLUMNS.items():
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.alter_column(column, type_=money_type, existing_type=existing_type)


def upgrade() -> None:
    _alter(sa.Numeric(18, 2), sa.Float())


def downgrade() -> None:
    _alter(sa.Float(), sa.Numeric(18, 2))
