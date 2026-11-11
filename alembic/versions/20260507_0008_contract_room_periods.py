from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260507_0008"
down_revision = "20260507_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("contract_rooms"):
        return

    columns = {column["name"] for column in inspector.get_columns("contract_rooms")}
    if "start_date" not in columns:
        op.add_column("contract_rooms", sa.Column("start_date", sa.Date(), nullable=True))
    if "end_date" not in columns:
        op.add_column("contract_rooms", sa.Column("end_date", sa.Date(), nullable=True))


def downgrade() -> None:
    pass
