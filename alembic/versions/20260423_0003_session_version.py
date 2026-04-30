from __future__ import annotations

from alembic import op
from sqlalchemy import inspect, text


revision = "20260423_0003"
down_revision = "20260423_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("users"):
        return

    columns = {column["name"] for column in inspector.get_columns("users")}
    if "session_version" not in columns:
        op.execute(text("ALTER TABLE users ADD COLUMN session_version INTEGER DEFAULT 0"))

    op.execute(text("UPDATE users SET session_version=0 WHERE session_version IS NULL"))


def downgrade() -> None:
    pass
