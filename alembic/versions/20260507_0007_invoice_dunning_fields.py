from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260507_0007"
down_revision = "20260507_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("receivable_records"):
        return

    columns = {column["name"] for column in inspector.get_columns("receivable_records")}
    additions = [
        ("invoice_no", sa.Column("invoice_no", sa.String(length=100), server_default="")),
        ("invoice_date", sa.Column("invoice_date", sa.Date(), nullable=True)),
        ("invoice_amount", sa.Column("invoice_amount", sa.Float(), server_default="0")),
        ("invoice_tax_rate", sa.Column("invoice_tax_rate", sa.Float(), server_default="0")),
        ("invoice_notes", sa.Column("invoice_notes", sa.Text(), server_default="")),
        ("last_dunning_date", sa.Column("last_dunning_date", sa.Date(), nullable=True)),
        ("next_follow_up_date", sa.Column("next_follow_up_date", sa.Date(), nullable=True)),
        ("dunning_notes", sa.Column("dunning_notes", sa.Text(), server_default="")),
    ]
    for name, column in additions:
        if name not in columns:
            op.add_column("receivable_records", column)


def downgrade() -> None:
    pass
