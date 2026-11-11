from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "20260508_0011"
down_revision = "20260508_0010"
branch_labels = None
depends_on = None


def _index_names(inspector, table_name: str) -> set[str]:
    if not inspector.has_table(table_name):
        return set()
    return {item["name"] for item in inspector.get_indexes(table_name) if item.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("customers"):
        op.create_table(
            "customers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("short_name", sa.String(length=100), nullable=True),
            sa.Column("credit_code", sa.String(length=50), nullable=True),
            sa.Column("customer_type", sa.String(length=50), nullable=True),
            sa.Column("industry", sa.String(length=100), nullable=True),
            sa.Column("contact_name", sa.String(length=50), nullable=True),
            sa.Column("contact_phone", sa.String(length=30), nullable=True),
            sa.Column("contact_email", sa.String(length=100), nullable=True),
            sa.Column("address", sa.Text(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=True, server_default="active"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    inspector = inspect(bind)
    customer_indexes = _index_names(inspector, "customers")
    if "ix_customers_name" not in customer_indexes:
        op.create_index("ix_customers_name", "customers", ["name"], unique=True)
    if "ix_customers_credit_code" not in customer_indexes:
        op.create_index("ix_customers_credit_code", "customers", ["credit_code"], unique=False)
    if "ix_customers_status" not in customer_indexes:
        op.create_index("ix_customers_status", "customers", ["status"], unique=False)

    if inspector.has_table("contracts"):
        contract_columns = {column["name"] for column in inspector.get_columns("contracts")}
        if "customer_id" not in contract_columns:
            op.add_column("contracts", sa.Column("customer_id", sa.Integer(), nullable=True))
            op.create_index("ix_contracts_customer_id", "contracts", ["customer_id"], unique=False)
        elif "ix_contracts_customer_id" not in _index_names(inspector, "contracts"):
            op.create_index("ix_contracts_customer_id", "contracts", ["customer_id"], unique=False)

        existing_names = {
            row[0]
            for row in bind.execute(text("SELECT name FROM customers WHERE name IS NOT NULL AND name != ''")).fetchall()
        }
        tenant_rows = bind.execute(
            text(
                """
                SELECT DISTINCT tenant_name
                FROM contracts
                WHERE tenant_name IS NOT NULL
                  AND trim(tenant_name) != ''
                ORDER BY tenant_name
                """
            )
        ).fetchall()
        for (tenant_name,) in tenant_rows:
            name = str(tenant_name)
            if not name.strip() or name in existing_names:
                continue
            bind.execute(
                text(
                    """
                    INSERT INTO customers
                    (name, status, created_at, updated_at)
                    VALUES (:name, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """
                ),
                {"name": name},
            )
            existing_names.add(name)

        bind.execute(
            text(
                """
                UPDATE contracts
                SET customer_id = (
                    SELECT customers.id
                    FROM customers
                    WHERE customers.name = contracts.tenant_name
                    LIMIT 1
                )
                WHERE customer_id IS NULL
                  AND tenant_name IS NOT NULL
                  AND trim(tenant_name) != ''
                """
            )
        )


def downgrade() -> None:
    pass
