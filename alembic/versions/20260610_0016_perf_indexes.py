"""常用过滤/排序字段索引：contracts.is_deleted/start_date/end_date、operation_logs.timestamp

Revision ID: 20260610_0016
Revises: 20260508_0015
Create Date: 2026-06-10
"""
from alembic import op


revision = "20260610_0016"
down_revision = "20260508_0015"
branch_labels = None
depends_on = None


INDEXES = [
    ("ix_contracts_is_deleted", "contracts", ["is_deleted"]),
    ("ix_contracts_start_date", "contracts", ["start_date"]),
    ("ix_contracts_end_date", "contracts", ["end_date"]),
    ("ix_operation_logs_timestamp", "operation_logs", ["timestamp"]),
]


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns, if_not_exists=True)


def downgrade() -> None:
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table, if_exists=True)
