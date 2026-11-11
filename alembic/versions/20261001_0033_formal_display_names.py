"""Use formal portfolio and building display names."""
from alembic import op
import sqlalchemy as sa
revision = "20261001_0033"
down_revision = "20260928_0032"
branch_labels = None
depends_on = None
def upgrade():
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE portfolios SET name=:new WHERE code=:code AND name=:old"), {"code":"DEMO_OFFICE_PORTFOLIO", "old":"示例资产组合（写字楼，暂定）", "new":"示例资产组合（写字楼）"})
    conn.execute(sa.text("UPDATE buildings SET name=:new WHERE code=:code AND name=:old"), {"code":"DEMO_A", "old":"示例双塔中心（暂定）", "new":"示例双塔中心"})
def downgrade():
    # Display-name normalization keeps the formal business name on rollback.
    pass
