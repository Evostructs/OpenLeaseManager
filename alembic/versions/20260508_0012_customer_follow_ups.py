from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260508_0012"
down_revision = "20260508_0011"
branch_labels = None
depends_on = None


def _index_names(inspector, table_name: str) -> set[str]:
    if not inspector.has_table(table_name):
        return set()
    return {item["name"] for item in inspector.get_indexes(table_name) if item.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("customer_follow_ups"):
        op.create_table(
            "customer_follow_ups",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
            sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id"), nullable=True),
            sa.Column("follow_up_type", sa.String(length=20), nullable=True, server_default="电话"),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("content", sa.Text(), nullable=True),
            sa.Column("next_action", sa.Text(), nullable=True),
            sa.Column("next_follow_up_date", sa.Date(), nullable=True),
            sa.Column("owner", sa.String(length=50), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=True, server_default="open"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    inspector = inspect(bind)
    indexes = _index_names(inspector, "customer_follow_ups")
    for name, columns in {
        "ix_customer_follow_ups_customer_id": ["customer_id"],
        "ix_customer_follow_ups_contract_id": ["contract_id"],
        "ix_customer_follow_ups_follow_up_type": ["follow_up_type"],
        "ix_customer_follow_ups_occurred_at": ["occurred_at"],
        "ix_customer_follow_ups_next_follow_up_date": ["next_follow_up_date"],
        "ix_customer_follow_ups_owner": ["owner"],
        "ix_customer_follow_ups_status": ["status"],
    }.items():
        if name not in indexes:
            op.create_index(name, "customer_follow_ups", columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("customer_follow_ups"):
        op.drop_table("customer_follow_ups")
