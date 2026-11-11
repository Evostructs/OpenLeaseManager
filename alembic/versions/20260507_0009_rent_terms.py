from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260507_0009"
down_revision = "20260507_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("rent_terms"):
        return

    op.create_table(
        "rent_terms",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id"), nullable=False),
        sa.Column("contract_room_id", sa.Integer(), sa.ForeignKey("contract_rooms.id"), nullable=True),
        sa.Column("room_no", sa.String(length=50), nullable=True),
        sa.Column("rule_type", sa.String(length=30), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("area", sa.Float(), nullable=True),
        sa.Column("unit_price", sa.Float(), nullable=True),
        sa.Column("monthly_amount", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_rent_terms_contract_id", "rent_terms", ["contract_id"])
    op.create_index("ix_rent_terms_contract_room_id", "rent_terms", ["contract_room_id"])
    op.create_index("ix_rent_terms_room_no", "rent_terms", ["room_no"])
    op.create_index("ix_rent_terms_rule_type", "rent_terms", ["rule_type"])


def downgrade() -> None:
    pass
