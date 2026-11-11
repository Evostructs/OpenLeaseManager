"""Add snapshot exchange source bindings, receipts, and receive capability.

Revision ID: 20260806_0028
Revises: 20260805_0027
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260806_0028"
down_revision = "20260805_0027"
branch_labels = None
depends_on = None


BINDING_TABLE = "snapshot_exchange_source_bindings"
RECEIPT_TABLE = "snapshot_exchange_receipts"
CAPABILITY = "snapshot_exchange_receive"
OLD_CAPABILITIES = (
    "platform_admin",
    "customer_master_manage",
    "data_import_submit",
    "snapshot_review",
    "snapshot_publish",
    "metric_definition_manage",
)
NEW_CAPABILITIES = (*OLD_CAPABILITIES, CAPABILITY)

BINDING_INDEXES = (
    "ix_snapshot_exchange_source_bindings_id",
    "ix_snapshot_exchange_source_bindings_source_id",
    "ix_snapshot_exchange_source_bindings_is_active",
    "ux_snapshot_exchange_bindings_code_normalized",
)
RECEIPT_INDEXES = (
    "ix_snapshot_exchange_receipts_id",
    "ix_snapshot_exchange_receipts_receipt_no",
    "ix_snapshot_exchange_receipts_binding_id",
    "ix_snapshot_exchange_receipts_delivery_id",
    "ix_snapshot_exchange_receipts_content_digest",
    "ix_snapshot_exchange_receipts_status",
    "ix_snapshot_exchange_receipts_period_start",
    "ix_snapshot_exchange_receipts_period_end",
    "ix_snapshot_exchange_receipts_as_of_date",
    "ix_snapshot_exchange_receipts_correction_of_receipt_id",
    "ix_snapshot_exchange_receipts_original_receipt_id",
    "ix_snapshot_exchange_receipts_candidate_snapshot_id",
    "ix_snapshot_exchange_receipts_candidate_batch_id",
    "ix_snapshot_exchange_receipts_candidate_ref",
    "ix_snapshot_exchange_receipts_actor_id",
    "ix_snapshot_exchange_receipts_received_at",
    "ux_snapshot_exchange_receipts_canonical_delivery",
    "ux_snapshot_exchange_receipts_accepted_content",
    "ux_snapshot_exchange_receipts_accepted_revision",
    "ux_snapshot_exchange_receipts_accepted_root",
    "ux_snapshot_exchange_receipts_accepted_correction",
    "ux_snapshot_exchange_receipts_accepted_snapshot",
    "ux_snapshot_exchange_receipts_accepted_batch",
    "ux_snapshot_exchange_receipts_accepted_candidate_ref",
)
RECEIPT_TRIGGERS = (
    "trg_snapshot_exchange_receipts_no_update",
    "trg_snapshot_exchange_receipts_no_delete",
)
RECEIPT_TRIGGER_SQL = (
    f"""
    CREATE TRIGGER {RECEIPT_TRIGGERS[0]}
    BEFORE UPDATE ON {RECEIPT_TABLE}
    BEGIN
        SELECT RAISE(ABORT, 'snapshot exchange receipts are immutable');
    END
    """,
    f"""
    CREATE TRIGGER {RECEIPT_TRIGGERS[1]}
    BEFORE DELETE ON {RECEIPT_TABLE}
    BEGIN
        SELECT RAISE(ABORT, 'snapshot exchange receipts are immutable');
    END
    """,
)
BINDING_IDENTITY_TRIGGER = "trg_snapshot_exchange_bindings_identity_guard"
BINDING_IDENTITY_TRIGGER_SQL = f"""
CREATE TRIGGER {BINDING_IDENTITY_TRIGGER}
BEFORE UPDATE OF source_id, source_system_code ON {BINDING_TABLE}
WHEN EXISTS (
    SELECT 1 FROM {RECEIPT_TABLE}
    WHERE binding_id = OLD.id
)
BEGIN
    SELECT RAISE(ABORT, 'snapshot exchange binding identity is immutable after receipt');
END
"""


def _create_receipt_immutability_guards(bind) -> None:
    for statement in RECEIPT_TRIGGER_SQL:
        bind.execute(sa.text(statement))


def _capability_check_sql(capabilities: tuple[str, ...]) -> str:
    values = ", ".join(f"'{item}'" for item in capabilities)
    return f"capability IN ({values})"


def _replace_capability_check(capabilities: tuple[str, ...]) -> None:
    with op.batch_alter_table("user_capabilities", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_user_capabilities_name", type_="check")
        batch_op.create_check_constraint(
            "ck_user_capabilities_name",
            _capability_check_sql(capabilities),
        )


def upgrade() -> None:
    _replace_capability_check(NEW_CAPABILITIES)

    op.create_table(
        BINDING_TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("source_system_code", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(trim(source_system_code)) > 0",
            name="ck_snapshot_exchange_bindings_code_nonempty",
        ),
        sa.CheckConstraint(
            "is_active IN (0, 1)",
            name="ck_snapshot_exchange_bindings_active_boolean",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", name="uq_snapshot_exchange_bindings_source"),
    )
    op.create_index(
        "ix_snapshot_exchange_source_bindings_id",
        BINDING_TABLE,
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_snapshot_exchange_source_bindings_source_id",
        BINDING_TABLE,
        ["source_id"],
        unique=False,
    )
    op.create_index(
        "ix_snapshot_exchange_source_bindings_is_active",
        BINDING_TABLE,
        ["is_active"],
        unique=False,
    )
    op.create_index(
        "ux_snapshot_exchange_bindings_code_normalized",
        BINDING_TABLE,
        [sa.text("lower(trim(source_system_code))")],
        unique=True,
    )

    op.create_table(
        RECEIPT_TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("receipt_no", sa.String(length=150), nullable=False),
        sa.Column("binding_id", sa.Integer(), nullable=False),
        sa.Column("delivery_id", sa.String(length=100), nullable=False),
        sa.Column("exchange_version", sa.String(length=50), nullable=False),
        sa.Column("metric_definition_version", sa.String(length=50), nullable=False),
        sa.Column("digest_profile", sa.String(length=50), nullable=False),
        sa.Column("digest_algorithm", sa.String(length=20), nullable=False),
        sa.Column("content_digest", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("source_publication_revision", sa.Integer(), nullable=False),
        sa.Column("source_publication_ref", sa.String(length=150), nullable=False),
        sa.Column("correction_of_receipt_id", sa.Integer(), nullable=True),
        sa.Column("original_receipt_id", sa.Integer(), nullable=True),
        sa.Column("candidate_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("candidate_batch_id", sa.Integer(), nullable=True),
        sa.Column("candidate_ref", sa.String(length=150), nullable=True),
        sa.Column("normalized_business_json", sa.Text(), nullable=False),
        sa.Column("errors_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_username", sa.String(length=100), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('accepted', 'duplicate', 'rejected', 'conflict')",
            name="ck_snapshot_exchange_receipts_status",
        ),
        sa.CheckConstraint(
            "length(trim(receipt_no)) > 0 "
            "AND length(trim(delivery_id)) > 0 "
            "AND length(trim(exchange_version)) > 0 "
            "AND length(trim(metric_definition_version)) > 0 "
            "AND length(trim(digest_profile)) > 0 "
            "AND length(trim(digest_algorithm)) > 0 "
            "AND length(trim(content_digest)) > 0 "
            "AND length(trim(source_publication_ref)) > 0 "
            "AND length(trim(normalized_business_json)) > 0 "
            "AND length(trim(errors_json)) > 0 "
            "AND length(trim(actor_username)) > 0",
            name="ck_snapshot_exchange_receipts_keys_nonempty",
        ),
        sa.CheckConstraint(
            "length(content_digest) = 64 "
            "AND content_digest = lower(content_digest) "
            "AND content_digest NOT GLOB '*[^0-9a-f]*'",
            name="ck_snapshot_exchange_receipts_digest_shape",
        ),
        sa.CheckConstraint(
            "period_end >= period_start AND as_of_date >= period_start AND as_of_date <= period_end",
            name="ck_snapshot_exchange_receipts_period",
        ),
        sa.CheckConstraint(
            "source_publication_revision > 0",
            name="ck_snapshot_exchange_receipts_source_revision",
        ),
        sa.CheckConstraint(
            "(status = 'accepted' "
            "AND original_receipt_id IS NULL "
            "AND candidate_snapshot_id IS NOT NULL "
            "AND candidate_batch_id IS NOT NULL "
            "AND candidate_ref IS NOT NULL "
            "AND length(trim(candidate_ref)) > 0) "
            "OR (status IN ('duplicate', 'conflict') "
            "AND original_receipt_id IS NOT NULL "
            "AND candidate_snapshot_id IS NULL "
            "AND candidate_batch_id IS NULL "
            "AND candidate_ref IS NULL) "
            "OR (status = 'rejected' "
            "AND original_receipt_id IS NULL "
            "AND candidate_snapshot_id IS NULL "
            "AND candidate_batch_id IS NULL "
            "AND candidate_ref IS NULL)",
            name="ck_snapshot_exchange_receipts_status_shape",
        ),
        sa.CheckConstraint(
            "correction_of_receipt_id IS NULL OR correction_of_receipt_id <> id",
            name="ck_snapshot_exchange_receipts_correction_not_self",
        ),
        sa.CheckConstraint(
            "original_receipt_id IS NULL OR original_receipt_id <> id",
            name="ck_snapshot_exchange_receipts_original_not_self",
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["binding_id"], [f"{BINDING_TABLE}.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["candidate_batch_id"],
            ["import_batches.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_snapshot_id"],
            ["building_operating_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["correction_of_receipt_id"],
            [f"{RECEIPT_TABLE}.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["original_receipt_id"],
            [f"{RECEIPT_TABLE}.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "binding_id",
            "delivery_id",
            "content_digest",
            "status",
            name="uq_snapshot_exchange_receipts_attempt",
        ),
    )
    for column_name in (
        "id",
        "binding_id",
        "delivery_id",
        "content_digest",
        "status",
        "period_start",
        "period_end",
        "as_of_date",
        "correction_of_receipt_id",
        "original_receipt_id",
        "candidate_snapshot_id",
        "candidate_batch_id",
        "candidate_ref",
        "actor_id",
        "received_at",
    ):
        op.create_index(
            f"ix_snapshot_exchange_receipts_{column_name}",
            RECEIPT_TABLE,
            [column_name],
            unique=False,
        )
    op.create_index(
        "ix_snapshot_exchange_receipts_receipt_no",
        RECEIPT_TABLE,
        ["receipt_no"],
        unique=True,
    )
    canonical_statuses = sa.text("status IN ('accepted', 'duplicate', 'rejected')")
    accepted_status = sa.text("status = 'accepted'")
    op.create_index(
        "ux_snapshot_exchange_receipts_canonical_delivery",
        RECEIPT_TABLE,
        ["binding_id", "delivery_id"],
        unique=True,
        sqlite_where=canonical_statuses,
        postgresql_where=canonical_statuses,
    )
    op.create_index(
        "ux_snapshot_exchange_receipts_accepted_content",
        RECEIPT_TABLE,
        ["binding_id", "content_digest"],
        unique=True,
        sqlite_where=accepted_status,
        postgresql_where=accepted_status,
    )
    op.create_index(
        "ux_snapshot_exchange_receipts_accepted_revision",
        RECEIPT_TABLE,
        [
            "binding_id",
            "period_start",
            "period_end",
            "as_of_date",
            "source_publication_revision",
        ],
        unique=True,
        sqlite_where=accepted_status,
        postgresql_where=accepted_status,
    )
    accepted_root = sa.text(
        "status = 'accepted' AND correction_of_receipt_id IS NULL"
    )
    op.create_index(
        "ux_snapshot_exchange_receipts_accepted_root",
        RECEIPT_TABLE,
        ["binding_id", "period_start", "period_end", "as_of_date"],
        unique=True,
        sqlite_where=accepted_root,
        postgresql_where=accepted_root,
    )
    accepted_correction = sa.text(
        "status = 'accepted' AND correction_of_receipt_id IS NOT NULL"
    )
    op.create_index(
        "ux_snapshot_exchange_receipts_accepted_correction",
        RECEIPT_TABLE,
        ["correction_of_receipt_id"],
        unique=True,
        sqlite_where=accepted_correction,
        postgresql_where=accepted_correction,
    )
    op.create_index(
        "ux_snapshot_exchange_receipts_accepted_snapshot",
        RECEIPT_TABLE,
        ["candidate_snapshot_id"],
        unique=True,
        sqlite_where=accepted_status,
        postgresql_where=accepted_status,
    )
    op.create_index(
        "ux_snapshot_exchange_receipts_accepted_batch",
        RECEIPT_TABLE,
        ["candidate_batch_id"],
        unique=True,
        sqlite_where=accepted_status,
        postgresql_where=accepted_status,
    )
    op.create_index(
        "ux_snapshot_exchange_receipts_accepted_candidate_ref",
        RECEIPT_TABLE,
        ["candidate_ref"],
        unique=True,
        sqlite_where=accepted_status,
        postgresql_where=accepted_status,
    )
    _create_receipt_immutability_guards(op.get_bind())
    op.execute(sa.text(BINDING_IDENTITY_TRIGGER_SQL))


def _downgrade_counts(bind) -> tuple[int, int, int]:
    binding_count = int(
        bind.execute(sa.text(f"SELECT COUNT(*) FROM {BINDING_TABLE}")).scalar_one()
        or 0
    )
    receipt_count = int(
        bind.execute(sa.text(f"SELECT COUNT(*) FROM {RECEIPT_TABLE}")).scalar_one()
        or 0
    )
    capability_count = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM user_capabilities WHERE capability = :capability"
            ),
            {"capability": CAPABILITY},
        ).scalar_one()
        or 0
    )
    return binding_count, receipt_count, capability_count


def downgrade() -> None:
    bind = op.get_bind()
    binding_count, receipt_count, capability_count = _downgrade_counts(bind)
    if binding_count or receipt_count or capability_count:
        raise RuntimeError(
            "阶段3F-1降级已停止：仍有来源绑定、交换回执或交换接收能力授权，"
            "降级会丢失交换治理数据；"
            f"bindings={binding_count}, receipts={receipt_count}, "
            f"capability_grants={capability_count}。"
        )

    op.execute(sa.text(f"DROP TRIGGER IF EXISTS {BINDING_IDENTITY_TRIGGER}"))
    for trigger_name in RECEIPT_TRIGGERS:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger_name}"))
    for index_name in reversed(RECEIPT_INDEXES):
        op.drop_index(index_name, table_name=RECEIPT_TABLE)
    op.drop_table(RECEIPT_TABLE)
    for index_name in reversed(BINDING_INDEXES):
        op.drop_index(index_name, table_name=BINDING_TABLE)
    op.drop_table(BINDING_TABLE)
    _replace_capability_check(OLD_CAPABILITIES)
