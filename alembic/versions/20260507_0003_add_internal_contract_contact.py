from __future__ import annotations

from alembic import op
from sqlalchemy import inspect


revision = "20260507_0003"
down_revision = "20260507_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("contracts"):
        existing = {column["name"] for column in inspector.get_columns("contracts")}
        additions = {
            "internal_contact_person": "VARCHAR(50) DEFAULT ''",
            "internal_contact_feishu_id": "VARCHAR(100) DEFAULT ''",
        }
        for name, ddl in additions.items():
            if name not in existing:
                op.execute(f"ALTER TABLE contracts ADD COLUMN {name} {ddl}")


def downgrade() -> None:
    pass
