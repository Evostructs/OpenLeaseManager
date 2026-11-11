from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "20260507_0004"
down_revision = "20260507_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("contract_rooms"):
        op.create_table(
            "contract_rooms",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id"), nullable=False),
            sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id"), nullable=True),
            sa.Column("tower", sa.String(length=10), server_default=""),
            sa.Column("room_no", sa.String(length=50), nullable=True),
            sa.Column("area", sa.Float(), server_default="0"),
            sa.Column("unit_price", sa.Float(), server_default="0"),
            sa.Column("monthly_rent", sa.Float(), server_default="0"),
            sa.Column("notes", sa.Text(), server_default=""),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_contract_rooms_contract_id", "contract_rooms", ["contract_id"])
        op.create_index("ix_contract_rooms_room_no", "contract_rooms", ["room_no"])

    if not inspector.has_table("receivable_records"):
        op.create_table(
            "receivable_records",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id"), nullable=False),
            sa.Column("period_month", sa.String(length=7), nullable=False),
            sa.Column("received_amount", sa.Float(), server_default="0"),
            sa.Column("received_date", sa.Date(), nullable=True),
            sa.Column("invoice_status", sa.String(length=20), server_default="未开票"),
            sa.Column("dunning_status", sa.String(length=20), server_default="未催收"),
            sa.Column("notes", sa.Text(), server_default=""),
            sa.Column("operator", sa.String(length=50), server_default=""),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("contract_id", "period_month", name="uq_receivable_contract_month"),
        )
        op.create_index("ix_receivable_records_contract_id", "receivable_records", ["contract_id"])
        op.create_index("ix_receivable_records_period_month", "receivable_records", ["period_month"])

    if inspector.has_table("contracts"):
        op.execute(
            text(
                """
                INSERT INTO contract_rooms (contract_id, room_id, tower, room_no, area, unit_price, monthly_rent, notes)
                SELECT
                    contracts.id,
                    rooms.id,
                    COALESCE(contracts.tower, ''),
                    contracts.room_no,
                    COALESCE(contracts.area, 0),
                    COALESCE(contracts.unit_price, 0),
                    COALESCE(contracts.monthly_rent, 0),
                    ''
                FROM contracts
                LEFT JOIN rooms ON rooms.room_no = contracts.room_no
                WHERE COALESCE(contracts.room_no, '') != ''
                  AND NOT EXISTS (
                    SELECT 1 FROM contract_rooms WHERE contract_rooms.contract_id = contracts.id
                  )
                """
            )
        )


def downgrade() -> None:
    pass
