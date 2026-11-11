from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260508_0010"
down_revision = "20260507_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("rent_terms"):
        return

    columns = {column["name"] for column in inspector.get_columns("rent_terms")}
    if "adjustment_type" not in columns:
        op.add_column("rent_terms", sa.Column("adjustment_type", sa.String(length=20), nullable=True))
    if "adjustment_value" not in columns:
        op.add_column("rent_terms", sa.Column("adjustment_value", sa.Float(), nullable=True))
    if "frequency" not in columns:
        op.add_column("rent_terms", sa.Column("frequency", sa.String(length=20), nullable=True))


def downgrade() -> None:
    pass
