from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260508_0015"
down_revision = "20260508_0014"
branch_labels = None
depends_on = None


def _columns(inspector, table_name: str) -> set[str]:
    if not inspector.has_table(table_name):
        return set()
    return {item["name"] for item in inspector.get_columns(table_name)}


def _index_names(inspector, table_name: str) -> set[str]:
    if not inspector.has_table(table_name):
        return set()
    return {item["name"] for item in inspector.get_indexes(table_name) if item.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = _columns(inspector, "operation_logs")
    if "target_type" not in columns:
        op.add_column("operation_logs", sa.Column("target_type", sa.String(length=30), nullable=True, server_default=""))
    if "target_id" not in columns:
        op.add_column("operation_logs", sa.Column("target_id", sa.Integer(), nullable=True))

    inspector = inspect(bind)
    indexes = _index_names(inspector, "operation_logs")
    if "ix_operation_logs_target_type" not in indexes:
        op.create_index("ix_operation_logs_target_type", "operation_logs", ["target_type"], unique=False)
    if "ix_operation_logs_target_id" not in indexes:
        op.create_index("ix_operation_logs_target_id", "operation_logs", ["target_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = _index_names(inspector, "operation_logs")
    if "ix_operation_logs_target_id" in indexes:
        op.drop_index("ix_operation_logs_target_id", table_name="operation_logs")
    if "ix_operation_logs_target_type" in indexes:
        op.drop_index("ix_operation_logs_target_type", table_name="operation_logs")
    columns = _columns(inspector, "operation_logs")
    if "target_id" in columns:
        op.drop_column("operation_logs", "target_id")
    if "target_type" in columns:
        op.drop_column("operation_logs", "target_type")
