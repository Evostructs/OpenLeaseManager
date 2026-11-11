from __future__ import annotations

from alembic import op
from sqlalchemy import inspect, text


revision = "20260507_0002"
down_revision = "20260507_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("contracts"):
        op.execute(
            text(
                """
                UPDATE contracts
                SET monthly_rent = ROUND(COALESCE(area, 0) * COALESCE(unit_price, 0) * 365.0 / 12.0, 2)
                """
            )
        )


def downgrade() -> None:
    pass
