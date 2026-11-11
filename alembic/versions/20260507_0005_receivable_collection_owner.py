from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260507_0005"
down_revision = "20260507_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("receivable_records"):
        return

    columns = {column["name"] for column in inspector.get_columns("receivable_records")}
    if "dunning_owner" not in columns:
        op.add_column("receivable_records", sa.Column("dunning_owner", sa.String(length=50), server_default=""))


def downgrade() -> None:
    pass
