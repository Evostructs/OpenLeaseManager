from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260430_0004"
down_revision = "20260423_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=True, server_default="bidirectional"),
        sa.Column("enabled", sa.Boolean(), nullable=True, server_default=sa.text("1")),
        sa.Column("config_json", sa.Text(), nullable=True, server_default="{}"),
        sa.Column("secret_json", sa.Text(), nullable=True, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_integration_connections_id", "integration_connections", ["id"])

    op.create_table(
        "integration_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey("integration_connections.id"), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True, server_default="{}"),
        sa.Column("status", sa.String(length=20), nullable=True, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_integration_events_id", "integration_events", ["id"])
    op.create_index("ix_integration_events_connection_id", "integration_events", ["connection_id"])
    op.create_index("ix_integration_events_status", "integration_events", ["status"])

    op.create_table(
        "integration_sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey("integration_connections.id"), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=True, server_default="bidirectional"),
        sa.Column("status", sa.String(length=20), nullable=True, server_default="reserved"),
        sa.Column("message", sa.Text(), nullable=True, server_default=""),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_integration_sync_runs_id", "integration_sync_runs", ["id"])
    op.create_index("ix_integration_sync_runs_connection_id", "integration_sync_runs", ["connection_id"])


def downgrade() -> None:
    op.drop_index("ix_integration_sync_runs_connection_id", table_name="integration_sync_runs")
    op.drop_index("ix_integration_sync_runs_id", table_name="integration_sync_runs")
    op.drop_table("integration_sync_runs")
    op.drop_index("ix_integration_events_status", table_name="integration_events")
    op.drop_index("ix_integration_events_connection_id", table_name="integration_events")
    op.drop_index("ix_integration_events_id", table_name="integration_events")
    op.drop_table("integration_events")
    op.drop_index("ix_integration_connections_id", table_name="integration_connections")
    op.drop_table("integration_connections")
