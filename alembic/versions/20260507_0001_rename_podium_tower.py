from __future__ import annotations

from alembic import op
from sqlalchemy import inspect, text


revision = "20260507_0001"
down_revision = "20260423_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("contracts"):
        op.execute(text("UPDATE contracts SET tower='商业配套' WHERE tower='南塔裙楼'"))

    if inspector.has_table("rooms"):
        op.execute(text("UPDATE rooms SET tower='商业配套' WHERE tower='南塔裙楼'"))


def downgrade() -> None:
    pass
