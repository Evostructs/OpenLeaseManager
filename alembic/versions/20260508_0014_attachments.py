from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260508_0014"
down_revision = "20260508_0013"
branch_labels = None
depends_on = None


def _index_names(inspector, table_name: str) -> set[str]:
    if not inspector.has_table(table_name):
        return set()
    return {item["name"] for item in inspector.get_indexes(table_name) if item.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("attachments"):
        op.create_table(
            "attachments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("entity_type", sa.String(length=30), nullable=False),
            sa.Column("entity_id", sa.Integer(), nullable=False),
            sa.Column("file_name", sa.String(length=255), nullable=False),
            sa.Column("storage_path", sa.String(length=500), nullable=False),
            sa.Column("content_type", sa.String(length=120), nullable=True, server_default=""),
            sa.Column("file_size", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("category", sa.String(length=30), nullable=True, server_default="其他"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("uploaded_by", sa.String(length=50), nullable=True, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
        )

    inspector = inspect(bind)
    indexes = _index_names(inspector, "attachments")
    for name, columns in {
        "ix_attachments_entity_type": ["entity_type"],
        "ix_attachments_entity_id": ["entity_id"],
        "ix_attachments_category": ["category"],
        "ix_attachments_deleted_at": ["deleted_at"],
        "ix_attachments_entity": ["entity_type", "entity_id", "deleted_at"],
    }.items():
        if name not in indexes:
            op.create_index(name, "attachments", columns, unique=False)
    if "ux_attachments_storage_path" not in indexes:
        op.create_index("ux_attachments_storage_path", "attachments", ["storage_path"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("attachments"):
        op.drop_table("attachments")
