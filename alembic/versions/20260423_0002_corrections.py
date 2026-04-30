from __future__ import annotations

from alembic import op
from sqlalchemy import inspect, text


revision = "20260423_0002"
down_revision = "20260423_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("users"):
        op.execute(text("UPDATE users SET role='editor' WHERE role IS NULL OR role='' OR role='user'"))
        op.execute(text("UPDATE users SET is_active=1 WHERE is_active IS NULL"))
        op.execute(text("UPDATE users SET display_name=username WHERE display_name IS NULL OR display_name=''"))

    if inspector.has_table("rooms"):
        op.execute(text("UPDATE rooms SET room_type='办公' WHERE room_type IS NULL OR room_type='' OR room_type='一巷'"))

    if inspector.has_table("contracts"):
        op.execute(text("UPDATE contracts SET is_deleted=0 WHERE is_deleted IS NULL"))


def downgrade() -> None:
    pass
