from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260423_0001"
down_revision = None
branch_labels = None
depends_on = None


def _column_names(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _ensure_index(inspector, table_name: str, index_name: str, columns: list[str], unique: bool = False) -> None:
    existing = {item["name"] for item in inspector.get_indexes(table_name)}
    if index_name not in existing:
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("contracts"):
        op.create_table(
            "contracts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("contract_no", sa.String(length=50)),
            sa.Column("tower", sa.String(length=10)),
            sa.Column("room_no", sa.String(length=50)),
            sa.Column("tenant_name", sa.String(length=100)),
            sa.Column("area", sa.Float(), server_default="0"),
            sa.Column("unit_price", sa.Float(), server_default="0"),
            sa.Column("lease_type", sa.String(length=20)),
            sa.Column("free_period", sa.Integer(), server_default="0"),
            sa.Column("monthly_rent", sa.Float(), server_default="0"),
            sa.Column("broker_fee", sa.Float(), server_default="0"),
            sa.Column("start_date", sa.Date()),
            sa.Column("end_date", sa.Date()),
            sa.Column("disposal", sa.String(length=20), server_default=""),
            sa.Column("contact_person", sa.String(length=50), server_default=""),
            sa.Column("contact_phone", sa.String(length=30), server_default=""),
            sa.Column("contact_email", sa.String(length=100), server_default=""),
            sa.Column("deposit", sa.Float(), server_default="0"),
            sa.Column("deposit_status", sa.String(length=20), server_default=""),
            sa.Column("property_fee_rate", sa.Float(), server_default="0"),
            sa.Column("property_fee_free", sa.Integer(), server_default="0"),
            sa.Column("prev_contract_no", sa.String(length=50), server_default=""),
            sa.Column("notes", sa.Text(), server_default=""),
            sa.Column("pdf_path", sa.String(length=500), server_default=""),
            sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
        )
    else:
        existing = _column_names(inspector, "contracts")
        additions = {
            "disposal": "VARCHAR(20) DEFAULT ''",
            "contact_person": "VARCHAR(50) DEFAULT ''",
            "contact_phone": "VARCHAR(30) DEFAULT ''",
            "contact_email": "VARCHAR(100) DEFAULT ''",
            "deposit": "FLOAT DEFAULT 0",
            "deposit_status": "VARCHAR(20) DEFAULT ''",
            "property_fee_rate": "FLOAT DEFAULT 0",
            "property_fee_free": "INTEGER DEFAULT 0",
            "prev_contract_no": "VARCHAR(50) DEFAULT ''",
            "pdf_path": "VARCHAR(500) DEFAULT ''",
            "is_deleted": "BOOLEAN DEFAULT 0",
            "created_at": "DATETIME",
            "updated_at": "DATETIME",
        }
        for name, ddl in additions.items():
            if name not in existing:
                op.execute(f"ALTER TABLE contracts ADD COLUMN {name} {ddl}")

    if not inspector.has_table("rooms"):
        op.create_table(
            "rooms",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tower", sa.String(length=10)),
            sa.Column("floor", sa.String(length=10)),
            sa.Column("room_no", sa.String(length=50)),
            sa.Column("room_type", sa.String(length=20), server_default="办公"),
            sa.Column("area", sa.Float(), server_default="0"),
            sa.Column("is_leasable", sa.Boolean(), server_default=sa.text("1")),
            sa.Column("notes", sa.Text(), server_default=""),
            sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
        )
    else:
        existing = _column_names(inspector, "rooms")
        additions = {
            "room_type": "VARCHAR(20) DEFAULT '办公'",
            "is_leasable": "BOOLEAN DEFAULT 1",
            "notes": "TEXT DEFAULT ''",
            "is_deleted": "BOOLEAN DEFAULT 0",
            "created_at": "DATETIME",
            "updated_at": "DATETIME",
        }
        for name, ddl in additions.items():
            if name not in existing:
                op.execute(f"ALTER TABLE rooms ADD COLUMN {name} {ddl}")

    if not inspector.has_table("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("username", sa.String(length=50)),
            sa.Column("display_name", sa.String(length=50), server_default=""),
            sa.Column("password_hash", sa.String(length=200)),
            sa.Column("role", sa.String(length=20), server_default="editor"),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("1")),
            sa.Column("created_at", sa.DateTime()),
        )
    else:
        existing = _column_names(inspector, "users")
        additions = {
            "display_name": "VARCHAR(50) DEFAULT ''",
            "role": "VARCHAR(20) DEFAULT 'editor'",
            "is_active": "BOOLEAN DEFAULT 1",
            "created_at": "DATETIME",
        }
        for name, ddl in additions.items():
            if name not in existing:
                op.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")

    if not inspector.has_table("operation_logs"):
        op.create_table(
            "operation_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id"), nullable=True),
            sa.Column("action", sa.String(length=20)),
            sa.Column("changes", sa.Text(), server_default=""),
            sa.Column("operator", sa.String(length=50), server_default="system"),
            sa.Column("timestamp", sa.DateTime()),
        )

    if not inspector.has_table("system_config"):
        op.create_table(
            "system_config",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("key", sa.String(length=50), unique=True),
            sa.Column("value", sa.Text()),
        )

    inspector = inspect(bind)
    _ensure_index(inspector, "contracts", "ix_contracts_id", ["id"])
    _ensure_index(inspector, "contracts", "ix_contracts_contract_no", ["contract_no"], unique=True)
    _ensure_index(inspector, "contracts", "ix_contracts_room_no", ["room_no"])
    _ensure_index(inspector, "contracts", "ix_contracts_tenant_name", ["tenant_name"])
    _ensure_index(inspector, "rooms", "ix_rooms_id", ["id"])
    _ensure_index(inspector, "rooms", "ix_rooms_room_no", ["room_no"], unique=True)
    _ensure_index(inspector, "users", "ix_users_id", ["id"])
    _ensure_index(inspector, "users", "ix_users_username", ["username"], unique=True)
    _ensure_index(inspector, "operation_logs", "ix_operation_logs_id", ["id"])


def downgrade() -> None:
    pass
