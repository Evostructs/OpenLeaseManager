"""多楼宇数据底座：只新增表和可空范围字段，不改现有业务语义。

Revision ID: 20260804_0019
Revises: 20260610_0018
Create Date: 2026-08-04
"""

revision = "20260804_0019"
down_revision = "20260610_0018"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


FOUNDATION_TABLES = (
    "area_measurements",
    "building_metric_facts",
    "building_operating_snapshots",
    "buildings",
    "customer_building_profiles",
    "data_sources",
    "import_batches",
    "import_errors",
    "metric_definitions",
    "portfolio_buildings",
    "portfolios",
    "source_records",
    "user_capabilities",
    "user_scope_grants",
    "zones",
)


def _assert_downgrade_discards_no_scope_data(bind) -> None:
    """Never turn a successful downgrade into silent multi-building data loss."""
    nonempty_tables = {
        table_name: int(
            bind.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()
        )
        for table_name in FOUNDATION_TABLES
    }
    scoped_legacy_rows = {
        "rooms": int(
            bind.execute(
                sa.text(
                    "SELECT COUNT(*) FROM rooms WHERE building_id IS NOT NULL OR zone_id IS NOT NULL"
                )
            ).scalar_one()
        ),
        "contracts": int(
            bind.execute(
                sa.text("SELECT COUNT(*) FROM contracts WHERE building_id IS NOT NULL")
            ).scalar_one()
        ),
        "contract_rooms": int(
            bind.execute(
                sa.text("SELECT COUNT(*) FROM contract_rooms WHERE building_id IS NOT NULL")
            ).scalar_one()
        ),
        "customer_follow_ups": int(
            bind.execute(
                sa.text(
                    "SELECT COUNT(*) FROM customer_follow_ups WHERE building_id IS NOT NULL"
                )
            ).scalar_one()
        ),
        "attachments": int(
            bind.execute(
                sa.text(
                    "SELECT COUNT(*) FROM attachments "
                    "WHERE scope_type IS NOT NULL OR portfolio_id IS NOT NULL OR building_id IS NOT NULL"
                )
            ).scalar_one()
        ),
        "operation_logs": int(
            bind.execute(
                sa.text(
                    "SELECT COUNT(*) FROM operation_logs "
                    "WHERE scope_type IS NOT NULL OR portfolio_id IS NOT NULL OR building_id IS NOT NULL"
                )
            ).scalar_one()
        ),
    }
    blockers = {
        **{f"table:{key}": value for key, value in nonempty_tables.items() if value},
        **{f"scope:{key}": value for key, value in scoped_legacy_rows.items() if value},
    }
    if blockers:
        summary = ", ".join(f"{key}={value}" for key, value in sorted(blockers.items()))
        raise RuntimeError(
            "多楼宇降级已停止：新表或新范围字段仍有数据；"
            "请先建立整包恢复点并显式处理这些数据。"
            f" {summary}"
        )


def upgrade() -> None:
    op.create_table('buildings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('ingestion_mode', sa.String(length=20), nullable=False),
    sa.Column('is_provisional', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("ingestion_mode IN ('detail', 'snapshot', 'external')", name='ck_buildings_ingestion_mode'),
    sa.CheckConstraint('length(trim(code)) > 0', name='ck_buildings_code_nonempty'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_buildings_code'), 'buildings', ['code'], unique=True)
    op.create_index(op.f('ix_buildings_id'), 'buildings', ['id'], unique=False)
    op.create_index(op.f('ix_buildings_is_active'), 'buildings', ['is_active'], unique=False)
    op.create_table('portfolios',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('is_provisional', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('length(trim(code)) > 0', name='ck_portfolios_code_nonempty'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_portfolios_code'), 'portfolios', ['code'], unique=True)
    op.create_index(op.f('ix_portfolios_id'), 'portfolios', ['id'], unique=False)
    op.create_index(op.f('ix_portfolios_is_active'), 'portfolios', ['is_active'], unique=False)
    op.create_table('building_operating_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('building_id', sa.Integer(), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('as_of_date', sa.Date(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('validation_status', sa.String(length=20), nullable=False),
    sa.Column('publication_status', sa.String(length=20), nullable=False),
    sa.Column('is_current', sa.Boolean(), nullable=False),
    sa.Column('submitted_by', sa.Integer(), nullable=True),
    sa.Column('submitted_at', sa.DateTime(), nullable=True),
    sa.Column('reviewed_by', sa.Integer(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    sa.Column('published_by', sa.Integer(), nullable=True),
    sa.Column('published_at', sa.DateTime(), nullable=True),
    sa.Column('supersedes_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("is_current = 0 OR publication_status = 'published'", name='ck_building_snapshots_current_published'),
    sa.CheckConstraint("publication_status IN ('draft', 'reviewed', 'published', 'superseded')", name='ck_building_snapshots_publication_status'),
    sa.CheckConstraint("publication_status NOT IN ('published', 'superseded') OR published_by IS NOT NULL", name='ck_building_snapshots_published_actor'),
    sa.CheckConstraint("validation_status IN ('pending', 'valid', 'rejected')", name='ck_building_snapshots_validation_status'),
    sa.CheckConstraint('(published_by IS NULL AND published_at IS NULL) OR (published_by IS NOT NULL AND published_at IS NOT NULL)', name='ck_building_snapshots_publication_pair'),
    sa.CheckConstraint('(reviewed_by IS NULL AND reviewed_at IS NULL) OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)', name='ck_building_snapshots_review_pair'),
    sa.CheckConstraint('(submitted_by IS NULL AND submitted_at IS NULL) OR (submitted_by IS NOT NULL AND submitted_at IS NOT NULL)', name='ck_building_snapshots_submission_pair'),
    sa.CheckConstraint('period_end >= period_start', name='ck_building_snapshots_period_range'),
    sa.CheckConstraint('revision > 0', name='ck_building_snapshots_revision'),
    sa.ForeignKeyConstraint(['building_id'], ['buildings.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['published_by'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['submitted_by'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['supersedes_id'], ['building_operating_snapshots.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('building_id', 'period_start', 'period_end', 'as_of_date', 'revision', name='uq_building_snapshots_revision')
    )
    op.create_index(op.f('ix_building_operating_snapshots_as_of_date'), 'building_operating_snapshots', ['as_of_date'], unique=False)
    op.create_index(op.f('ix_building_operating_snapshots_building_id'), 'building_operating_snapshots', ['building_id'], unique=False)
    op.create_index(op.f('ix_building_operating_snapshots_id'), 'building_operating_snapshots', ['id'], unique=False)
    op.create_index(op.f('ix_building_operating_snapshots_is_current'), 'building_operating_snapshots', ['is_current'], unique=False)
    op.create_index(op.f('ix_building_operating_snapshots_period_end'), 'building_operating_snapshots', ['period_end'], unique=False)
    op.create_index(op.f('ix_building_operating_snapshots_period_start'), 'building_operating_snapshots', ['period_start'], unique=False)
    op.create_index(op.f('ix_building_operating_snapshots_publication_status'), 'building_operating_snapshots', ['publication_status'], unique=False)
    op.create_index(op.f('ix_building_operating_snapshots_supersedes_id'), 'building_operating_snapshots', ['supersedes_id'], unique=False)
    op.create_index(op.f('ix_building_operating_snapshots_validation_status'), 'building_operating_snapshots', ['validation_status'], unique=False)
    op.create_index('ux_building_snapshots_current_published', 'building_operating_snapshots', ['building_id', 'period_start', 'period_end', 'as_of_date'], unique=True, sqlite_where=sa.text("publication_status = 'published' AND is_current = 1"))
    op.create_table('customer_building_profiles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False),
    sa.Column('building_id', sa.Integer(), nullable=False),
    sa.Column('local_contact', sa.Text(), nullable=False),
    sa.Column('local_notes', sa.Text(), nullable=False),
    sa.Column('owner', sa.String(length=100), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name='ck_customer_building_profiles_valid_range'),
    sa.ForeignKeyConstraint(['building_id'], ['buildings.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('customer_id', 'building_id', 'valid_from', name='uq_customer_building_profile_from')
    )
    op.create_index(op.f('ix_customer_building_profiles_building_id'), 'customer_building_profiles', ['building_id'], unique=False)
    op.create_index(op.f('ix_customer_building_profiles_customer_id'), 'customer_building_profiles', ['customer_id'], unique=False)
    op.create_index(op.f('ix_customer_building_profiles_id'), 'customer_building_profiles', ['id'], unique=False)
    op.create_table('data_sources',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('building_id', sa.Integer(), nullable=False),
    sa.Column('source_type', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('owner', sa.String(length=100), nullable=False),
    sa.Column('cadence', sa.String(length=50), nullable=False),
    sa.Column('freshness_rule', sa.Text(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("source_type IN ('detail', 'manual_snapshot', 'excel', 'external_api')", name='ck_data_sources_type'),
    sa.ForeignKeyConstraint(['building_id'], ['buildings.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('building_id', 'name', name='uq_data_sources_building_name')
    )
    op.create_index(op.f('ix_data_sources_building_id'), 'data_sources', ['building_id'], unique=False)
    op.create_index(op.f('ix_data_sources_id'), 'data_sources', ['id'], unique=False)
    op.create_index(op.f('ix_data_sources_is_active'), 'data_sources', ['is_active'], unique=False)
    op.create_index(op.f('ix_data_sources_source_type'), 'data_sources', ['source_type'], unique=False)
    op.create_table('metric_definitions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('business_definition', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('grain', sa.String(length=30), nullable=False),
    sa.Column('unit', sa.String(length=50), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('numerator_rule', sa.Text(), nullable=False),
    sa.Column('denominator_rule', sa.Text(), nullable=False),
    sa.Column('inclusion_rule', sa.Text(), nullable=False),
    sa.Column('exclusion_rule', sa.Text(), nullable=False),
    sa.Column('aggregation_rule', sa.Text(), nullable=False),
    sa.Column('coverage_rule', sa.Text(), nullable=False),
    sa.Column('estimation_policy', sa.Text(), nullable=False),
    sa.Column('freshness_policy', sa.Text(), nullable=False),
    sa.Column('owner', sa.String(length=100), nullable=False),
    sa.Column('definition_hash', sa.String(length=128), nullable=False),
    sa.Column('publication_status', sa.String(length=20), nullable=False),
    sa.Column('published_by', sa.Integer(), nullable=True),
    sa.Column('published_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("(publication_status = 'draft' AND published_by IS NULL AND published_at IS NULL) OR (publication_status = 'published' AND published_by IS NOT NULL AND published_at IS NOT NULL)", name='ck_metric_definitions_publication_pair'),
    sa.CheckConstraint("publication_status IN ('draft', 'published')", name='ck_metric_definitions_publication_status'),
    sa.CheckConstraint('length(trim(code)) > 0', name='ck_metric_definitions_code_nonempty'),
    sa.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name='ck_metric_definitions_valid_range'),
    sa.CheckConstraint('version > 0', name='ck_metric_definitions_version'),
    sa.ForeignKeyConstraint(['published_by'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code', 'version', name='uq_metric_definitions_code_version')
    )
    op.create_index(op.f('ix_metric_definitions_code'), 'metric_definitions', ['code'], unique=False)
    op.create_index(op.f('ix_metric_definitions_id'), 'metric_definitions', ['id'], unique=False)
    op.create_index(op.f('ix_metric_definitions_publication_status'), 'metric_definitions', ['publication_status'], unique=False)
    op.create_table('portfolio_buildings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('portfolio_id', sa.Integer(), nullable=False),
    sa.Column('building_id', sa.Integer(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name='ck_portfolio_buildings_valid_range'),
    sa.ForeignKeyConstraint(['building_id'], ['buildings.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['portfolio_id'], ['portfolios.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('portfolio_id', 'building_id', 'valid_from', name='uq_portfolio_building_from')
    )
    op.create_index(op.f('ix_portfolio_buildings_building_id'), 'portfolio_buildings', ['building_id'], unique=False)
    op.create_index(op.f('ix_portfolio_buildings_portfolio_id'), 'portfolio_buildings', ['portfolio_id'], unique=False)
    op.create_table('user_capabilities',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('capability', sa.String(length=50), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('granted_by', sa.Integer(), nullable=True),
    sa.Column('granted_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("capability IN ('platform_admin', 'customer_master_manage', 'data_import_submit', 'snapshot_review', 'snapshot_publish', 'metric_definition_manage')", name='ck_user_capabilities_name'),
    sa.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name='ck_user_capabilities_valid_range'),
    sa.ForeignKeyConstraint(['granted_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'capability', 'valid_from', name='uq_user_capability_from')
    )
    op.create_index(op.f('ix_user_capabilities_capability'), 'user_capabilities', ['capability'], unique=False)
    op.create_index(op.f('ix_user_capabilities_id'), 'user_capabilities', ['id'], unique=False)
    op.create_index(op.f('ix_user_capabilities_user_id'), 'user_capabilities', ['user_id'], unique=False)
    op.create_table('user_scope_grants',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('scope_type', sa.String(length=20), nullable=False),
    sa.Column('portfolio_id', sa.Integer(), nullable=True),
    sa.Column('building_id', sa.Integer(), nullable=True),
    sa.Column('access_level', sa.String(length=10), nullable=False),
    sa.Column('include_future_buildings', sa.Boolean(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('granted_by', sa.Integer(), nullable=True),
    sa.Column('granted_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("(scope_type = 'portfolio' AND portfolio_id IS NOT NULL AND building_id IS NULL) OR (scope_type = 'building' AND building_id IS NOT NULL AND portfolio_id IS NULL)", name='ck_user_scope_grants_target'),
    sa.CheckConstraint("access_level IN ('view', 'edit')", name='ck_user_scope_grants_access_level'),
    sa.CheckConstraint("scope_type = 'portfolio' OR include_future_buildings = 0", name='ck_user_scope_grants_future_scope'),
    sa.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name='ck_user_scope_grants_valid_range'),
    sa.ForeignKeyConstraint(['building_id'], ['buildings.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['granted_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['portfolio_id'], ['portfolios.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'building_id', 'valid_from', name='uq_scope_user_building_from'),
    sa.UniqueConstraint('user_id', 'portfolio_id', 'valid_from', name='uq_scope_user_portfolio_from')
    )
    op.create_index(op.f('ix_user_scope_grants_building_id'), 'user_scope_grants', ['building_id'], unique=False)
    op.create_index(op.f('ix_user_scope_grants_id'), 'user_scope_grants', ['id'], unique=False)
    op.create_index(op.f('ix_user_scope_grants_portfolio_id'), 'user_scope_grants', ['portfolio_id'], unique=False)
    op.create_index(op.f('ix_user_scope_grants_scope_type'), 'user_scope_grants', ['scope_type'], unique=False)
    op.create_index(op.f('ix_user_scope_grants_user_id'), 'user_scope_grants', ['user_id'], unique=False)
    op.create_table('zones',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('building_id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('zone_type', sa.String(length=30), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('length(trim(code)) > 0', name='ck_zones_code_nonempty'),
    sa.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name='ck_zones_valid_range'),
    sa.ForeignKeyConstraint(['building_id'], ['buildings.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('building_id', 'code', name='uq_zones_building_code')
    )
    op.create_index(op.f('ix_zones_building_id'), 'zones', ['building_id'], unique=False)
    op.create_index(op.f('ix_zones_id'), 'zones', ['id'], unique=False)
    op.create_index(op.f('ix_zones_is_active'), 'zones', ['is_active'], unique=False)
    op.create_table('import_batches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('source_file_name', sa.String(length=255), nullable=True),
    sa.Column('external_batch_no', sa.String(length=100), nullable=True),
    sa.Column('period', sa.String(length=20), nullable=True),
    sa.Column('as_of_date', sa.Date(), nullable=False),
    sa.Column('source_observed_at', sa.DateTime(), nullable=True),
    sa.Column('source_updated_at', sa.DateTime(), nullable=True),
    sa.Column('received_at', sa.DateTime(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('ingested_at', sa.DateTime(), nullable=True),
    sa.Column('published_at', sa.DateTime(), nullable=True),
    sa.Column('file_checksum', sa.String(length=128), nullable=True),
    sa.Column('parser_version', sa.String(length=100), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('accepted_count', sa.Integer(), nullable=False),
    sa.Column('rejected_count', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error_summary', sa.Text(), nullable=False),
    sa.Column('reviewed_by', sa.Integer(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    sa.Column('supersedes_id', sa.Integer(), nullable=True),
    sa.Column('correction_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("status IN ('received', 'validating', 'rejected', 'reviewed', 'published', 'superseded')", name='ck_import_batches_status'),
    sa.CheckConstraint('(reviewed_by IS NULL AND reviewed_at IS NULL) OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)', name='ck_import_batches_review_pair'),
    sa.CheckConstraint('row_count >= 0 AND accepted_count >= 0 AND rejected_count >= 0 AND accepted_count + rejected_count <= row_count', name='ck_import_batches_counts'),
    sa.CheckConstraint('supersedes_id IS NULL OR (correction_reason IS NOT NULL AND length(trim(correction_reason)) > 0)', name='ck_import_batches_correction_reason'),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['supersedes_id'], ['import_batches.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'external_batch_no', name='uq_import_batches_external_no'),
    sa.UniqueConstraint('source_id', 'file_checksum', name='uq_import_batches_file_checksum')
    )
    op.create_index(op.f('ix_import_batches_as_of_date'), 'import_batches', ['as_of_date'], unique=False)
    op.create_index(op.f('ix_import_batches_id'), 'import_batches', ['id'], unique=False)
    op.create_index(op.f('ix_import_batches_period'), 'import_batches', ['period'], unique=False)
    op.create_index(op.f('ix_import_batches_source_id'), 'import_batches', ['source_id'], unique=False)
    op.create_index(op.f('ix_import_batches_status'), 'import_batches', ['status'], unique=False)
    op.create_index(op.f('ix_import_batches_supersedes_id'), 'import_batches', ['supersedes_id'], unique=False)
    op.create_table('source_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('batch_id', sa.Integer(), nullable=False),
    sa.Column('external_record_id', sa.String(length=150), nullable=True),
    sa.Column('row_no', sa.Integer(), nullable=True),
    sa.Column('record_checksum', sa.String(length=128), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('business_object_type', sa.String(length=50), nullable=True),
    sa.Column('business_object_id', sa.Integer(), nullable=True),
    sa.Column('source_ref', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name='ck_source_records_status'),
    sa.CheckConstraint('external_record_id IS NOT NULL OR row_no IS NOT NULL', name='ck_source_records_locator'),
    sa.CheckConstraint('row_no IS NULL OR row_no > 0', name='ck_source_records_positive_row'),
    sa.ForeignKeyConstraint(['batch_id'], ['import_batches.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('batch_id', 'external_record_id', name='uq_source_records_external_id'),
    sa.UniqueConstraint('batch_id', 'row_no', name='uq_source_records_row_no')
    )
    op.create_index(op.f('ix_source_records_batch_id'), 'source_records', ['batch_id'], unique=False)
    op.create_index(op.f('ix_source_records_business_object_id'), 'source_records', ['business_object_id'], unique=False)
    op.create_index(op.f('ix_source_records_business_object_type'), 'source_records', ['business_object_type'], unique=False)
    op.create_index(op.f('ix_source_records_id'), 'source_records', ['id'], unique=False)
    op.create_index(op.f('ix_source_records_status'), 'source_records', ['status'], unique=False)
    op.create_table('area_measurements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('building_id', sa.Integer(), nullable=False),
    sa.Column('zone_id', sa.Integer(), nullable=True),
    sa.Column('room_id', sa.Integer(), nullable=True),
    sa.Column('area_type', sa.String(length=50), nullable=False),
    sa.Column('measurement_purpose', sa.String(length=100), nullable=False),
    sa.Column('area_sqm', sa.Numeric(precision=18, scale=4, asdecimal=False), nullable=False),
    sa.Column('standard', sa.String(length=100), nullable=False),
    sa.Column('boundary_method', sa.Text(), nullable=False),
    sa.Column('measured_at', sa.Date(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('source_record_id', sa.Integer(), nullable=False),
    sa.Column('evidence_ref', sa.Text(), nullable=False),
    sa.Column('approved_by', sa.Integer(), nullable=True),
    sa.Column('approved_at', sa.DateTime(), nullable=True),
    sa.Column('quality_status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("quality_status IN ('draft', 'reviewed', 'approved', 'rejected')", name='ck_area_measurements_quality_status'),
    sa.CheckConstraint('(approved_by IS NULL AND approved_at IS NULL) OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)', name='ck_area_measurements_approval_pair'),
    sa.CheckConstraint('area_sqm >= 0', name='ck_area_measurements_nonnegative'),
    sa.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name='ck_area_measurements_valid_range'),
    sa.CheckConstraint('version > 0', name='ck_area_measurements_version'),
    sa.CheckConstraint('zone_id IS NULL OR room_id IS NULL', name='ck_area_measurements_single_target'),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['building_id'], ['buildings.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_record_id'], ['source_records.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['zone_id'], ['zones.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_record_id', 'area_type', 'measurement_purpose', 'version', name='uq_area_measurements_source_kind_version')
    )
    op.create_index(op.f('ix_area_measurements_area_type'), 'area_measurements', ['area_type'], unique=False)
    op.create_index(op.f('ix_area_measurements_building_id'), 'area_measurements', ['building_id'], unique=False)
    op.create_index(op.f('ix_area_measurements_id'), 'area_measurements', ['id'], unique=False)
    op.create_index(op.f('ix_area_measurements_quality_status'), 'area_measurements', ['quality_status'], unique=False)
    op.create_index(op.f('ix_area_measurements_room_id'), 'area_measurements', ['room_id'], unique=False)
    op.create_index(op.f('ix_area_measurements_source_record_id'), 'area_measurements', ['source_record_id'], unique=False)
    op.create_index(op.f('ix_area_measurements_zone_id'), 'area_measurements', ['zone_id'], unique=False)
    op.create_table('building_metric_facts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('snapshot_id', sa.Integer(), nullable=False),
    sa.Column('metric_definition_id', sa.Integer(), nullable=False),
    sa.Column('value', sa.Numeric(precision=24, scale=6, asdecimal=False), nullable=True),
    sa.Column('numerator', sa.Numeric(precision=24, scale=6, asdecimal=False), nullable=True),
    sa.Column('denominator', sa.Numeric(precision=24, scale=6, asdecimal=False), nullable=True),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('batch_id', sa.Integer(), nullable=True),
    sa.Column('source_record_id', sa.Integer(), nullable=True),
    sa.Column('eligible_buildings', sa.Integer(), nullable=False),
    sa.Column('included_buildings', sa.Integer(), nullable=False),
    sa.Column('covered_area_sqm', sa.Numeric(precision=18, scale=4, asdecimal=False), nullable=True),
    sa.Column('eligible_area_sqm', sa.Numeric(precision=18, scale=4, asdecimal=False), nullable=True),
    sa.Column('covered_days', sa.Integer(), nullable=False),
    sa.Column('eligible_days', sa.Integer(), nullable=False),
    sa.Column('estimate_share', sa.Numeric(precision=10, scale=6, asdecimal=False), nullable=True),
    sa.Column('estimate_method', sa.Text(), nullable=False),
    sa.Column('data_depth', sa.String(length=20), nullable=False),
    sa.Column('completeness_status', sa.String(length=20), nullable=False),
    sa.Column('freshness_status', sa.String(length=20), nullable=False),
    sa.Column('validation_status', sa.String(length=20), nullable=False),
    sa.Column('publication_status', sa.String(length=20), nullable=False),
    sa.Column('calculation_version', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("completeness_status IN ('unknown', 'partial', 'complete')", name='ck_building_metric_facts_completeness_status'),
    sa.CheckConstraint("data_depth IN ('detail', 'snapshot', 'external')", name='ck_building_metric_facts_data_depth'),
    sa.CheckConstraint("freshness_status IN ('unknown', 'current', 'stale')", name='ck_building_metric_facts_freshness_status'),
    sa.CheckConstraint("publication_status IN ('draft', 'published', 'superseded')", name='ck_building_metric_facts_publication_status'),
    sa.CheckConstraint("validation_status IN ('pending', 'valid', 'rejected')", name='ck_building_metric_facts_validation_status'),
    sa.CheckConstraint('covered_area_sqm IS NULL OR covered_area_sqm >= 0', name='ck_building_metric_facts_covered_area'),
    sa.CheckConstraint('covered_area_sqm IS NULL OR eligible_area_sqm IS NULL OR covered_area_sqm <= eligible_area_sqm', name='ck_building_metric_facts_area_coverage'),
    sa.CheckConstraint('denominator IS NULL OR denominator >= 0', name='ck_building_metric_facts_denominator'),
    sa.CheckConstraint('eligible_area_sqm IS NULL OR eligible_area_sqm >= 0', name='ck_building_metric_facts_eligible_area'),
    sa.CheckConstraint('eligible_buildings >= 0 AND included_buildings >= 0 AND included_buildings <= eligible_buildings', name='ck_building_metric_facts_building_coverage'),
    sa.CheckConstraint('eligible_days >= 0 AND covered_days >= 0 AND covered_days <= eligible_days', name='ck_building_metric_facts_day_coverage'),
    sa.CheckConstraint('estimate_share IS NULL OR (estimate_share >= 0 AND estimate_share <= 1)', name='ck_building_metric_facts_estimate_share'),
    sa.CheckConstraint('numerator IS NULL OR numerator >= 0', name='ck_building_metric_facts_numerator'),
    sa.ForeignKeyConstraint(['batch_id'], ['import_batches.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['metric_definition_id'], ['metric_definitions.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['snapshot_id'], ['building_operating_snapshots.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_record_id'], ['source_records.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('snapshot_id', 'metric_definition_id', name='uq_building_metric_fact_definition')
    )
    op.create_index(op.f('ix_building_metric_facts_batch_id'), 'building_metric_facts', ['batch_id'], unique=False)
    op.create_index(op.f('ix_building_metric_facts_id'), 'building_metric_facts', ['id'], unique=False)
    op.create_index(op.f('ix_building_metric_facts_metric_definition_id'), 'building_metric_facts', ['metric_definition_id'], unique=False)
    op.create_index(op.f('ix_building_metric_facts_snapshot_id'), 'building_metric_facts', ['snapshot_id'], unique=False)
    op.create_index(op.f('ix_building_metric_facts_source_id'), 'building_metric_facts', ['source_id'], unique=False)
    op.create_index(op.f('ix_building_metric_facts_source_record_id'), 'building_metric_facts', ['source_record_id'], unique=False)
    op.create_table('import_errors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('batch_id', sa.Integer(), nullable=False),
    sa.Column('source_record_id', sa.Integer(), nullable=True),
    sa.Column('row_no', sa.Integer(), nullable=True),
    sa.Column('field_name', sa.String(length=100), nullable=True),
    sa.Column('error_code', sa.String(length=100), nullable=False),
    sa.Column('severity', sa.String(length=20), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("severity IN ('warning', 'error', 'blocking')", name='ck_import_errors_severity'),
    sa.CheckConstraint('length(trim(error_code)) > 0', name='ck_import_errors_code_nonempty'),
    sa.CheckConstraint('row_no IS NULL OR row_no > 0', name='ck_import_errors_positive_row'),
    sa.ForeignKeyConstraint(['batch_id'], ['import_batches.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_record_id'], ['source_records.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_import_errors_batch_id'), 'import_errors', ['batch_id'], unique=False)
    op.create_index(op.f('ix_import_errors_error_code'), 'import_errors', ['error_code'], unique=False)
    op.create_index(op.f('ix_import_errors_id'), 'import_errors', ['id'], unique=False)
    op.create_index(op.f('ix_import_errors_severity'), 'import_errors', ['severity'], unique=False)
    op.create_index(op.f('ix_import_errors_source_record_id'), 'import_errors', ['source_record_id'], unique=False)
    # SQLite 需通过 batch 重建表才能安全增加外键；新字段全部可空，
    # 因而上一版应用仍能读写扩展后的数据库。
    with op.batch_alter_table("attachments") as batch:
        batch.add_column(sa.Column("scope_type", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("portfolio_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("building_id", sa.Integer(), nullable=True))
        batch.create_index("ix_attachments_scope_type", ["scope_type"], unique=False)
        batch.create_index("ix_attachments_portfolio_id", ["portfolio_id"], unique=False)
        batch.create_index("ix_attachments_building_id", ["building_id"], unique=False)
        batch.create_foreign_key(
            "fk_attachments_portfolio_id_portfolios",
            "portfolios",
            ["portfolio_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_attachments_building_id_buildings",
            "buildings",
            ["building_id"],
            ["id"],
        )

    with op.batch_alter_table("contract_rooms") as batch:
        batch.add_column(sa.Column("building_id", sa.Integer(), nullable=True))
        batch.create_index("ix_contract_rooms_building_id", ["building_id"], unique=False)
        batch.create_foreign_key(
            "fk_contract_rooms_building_id_buildings",
            "buildings",
            ["building_id"],
            ["id"],
        )

    with op.batch_alter_table("contracts") as batch:
        batch.add_column(sa.Column("building_id", sa.Integer(), nullable=True))
        batch.create_index("ix_contracts_building_id", ["building_id"], unique=False)
        batch.create_foreign_key(
            "fk_contracts_building_id_buildings",
            "buildings",
            ["building_id"],
            ["id"],
        )

    with op.batch_alter_table("customer_follow_ups") as batch:
        batch.add_column(sa.Column("building_id", sa.Integer(), nullable=True))
        batch.create_index("ix_customer_follow_ups_building_id", ["building_id"], unique=False)
        batch.create_foreign_key(
            "fk_customer_follow_ups_building_id_buildings",
            "buildings",
            ["building_id"],
            ["id"],
        )

    with op.batch_alter_table("operation_logs") as batch:
        batch.add_column(sa.Column("scope_type", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("portfolio_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("building_id", sa.Integer(), nullable=True))
        batch.create_index("ix_operation_logs_scope_type", ["scope_type"], unique=False)
        batch.create_index("ix_operation_logs_portfolio_id", ["portfolio_id"], unique=False)
        batch.create_index("ix_operation_logs_building_id", ["building_id"], unique=False)
        batch.create_foreign_key(
            "fk_operation_logs_portfolio_id_portfolios",
            "portfolios",
            ["portfolio_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_operation_logs_building_id_buildings",
            "buildings",
            ["building_id"],
            ["id"],
        )

    with op.batch_alter_table("rooms") as batch:
        batch.add_column(sa.Column("building_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("zone_id", sa.Integer(), nullable=True))
        batch.create_index("ix_rooms_building_id", ["building_id"], unique=False)
        batch.create_index("ix_rooms_zone_id", ["zone_id"], unique=False)
        batch.create_foreign_key(
            "fk_rooms_building_id_buildings",
            "buildings",
            ["building_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_rooms_zone_id_zones",
            "zones",
            ["zone_id"],
            ["id"],
        )


def downgrade() -> None:
    _assert_downgrade_discards_no_scope_data(op.get_bind())
    # 先移除旧表对新主数据的引用，再按依赖顺序删除新表。
    with op.batch_alter_table("rooms") as batch:
        batch.drop_constraint("fk_rooms_zone_id_zones", type_="foreignkey")
        batch.drop_constraint("fk_rooms_building_id_buildings", type_="foreignkey")
        batch.drop_index("ix_rooms_zone_id")
        batch.drop_index("ix_rooms_building_id")
        batch.drop_column("zone_id")
        batch.drop_column("building_id")

    with op.batch_alter_table("operation_logs") as batch:
        batch.drop_constraint("fk_operation_logs_building_id_buildings", type_="foreignkey")
        batch.drop_constraint("fk_operation_logs_portfolio_id_portfolios", type_="foreignkey")
        batch.drop_index("ix_operation_logs_building_id")
        batch.drop_index("ix_operation_logs_portfolio_id")
        batch.drop_index("ix_operation_logs_scope_type")
        batch.drop_column("building_id")
        batch.drop_column("portfolio_id")
        batch.drop_column("scope_type")

    with op.batch_alter_table("customer_follow_ups") as batch:
        batch.drop_constraint("fk_customer_follow_ups_building_id_buildings", type_="foreignkey")
        batch.drop_index("ix_customer_follow_ups_building_id")
        batch.drop_column("building_id")

    with op.batch_alter_table("contracts") as batch:
        batch.drop_constraint("fk_contracts_building_id_buildings", type_="foreignkey")
        batch.drop_index("ix_contracts_building_id")
        batch.drop_column("building_id")

    with op.batch_alter_table("contract_rooms") as batch:
        batch.drop_constraint("fk_contract_rooms_building_id_buildings", type_="foreignkey")
        batch.drop_index("ix_contract_rooms_building_id")
        batch.drop_column("building_id")

    with op.batch_alter_table("attachments") as batch:
        batch.drop_constraint("fk_attachments_building_id_buildings", type_="foreignkey")
        batch.drop_constraint("fk_attachments_portfolio_id_portfolios", type_="foreignkey")
        batch.drop_index("ix_attachments_building_id")
        batch.drop_index("ix_attachments_portfolio_id")
        batch.drop_index("ix_attachments_scope_type")
        batch.drop_column("building_id")
        batch.drop_column("portfolio_id")
        batch.drop_column("scope_type")
    op.drop_index(op.f('ix_import_errors_source_record_id'), table_name='import_errors')
    op.drop_index(op.f('ix_import_errors_severity'), table_name='import_errors')
    op.drop_index(op.f('ix_import_errors_id'), table_name='import_errors')
    op.drop_index(op.f('ix_import_errors_error_code'), table_name='import_errors')
    op.drop_index(op.f('ix_import_errors_batch_id'), table_name='import_errors')
    op.drop_table('import_errors')
    op.drop_index(op.f('ix_building_metric_facts_source_record_id'), table_name='building_metric_facts')
    op.drop_index(op.f('ix_building_metric_facts_source_id'), table_name='building_metric_facts')
    op.drop_index(op.f('ix_building_metric_facts_snapshot_id'), table_name='building_metric_facts')
    op.drop_index(op.f('ix_building_metric_facts_metric_definition_id'), table_name='building_metric_facts')
    op.drop_index(op.f('ix_building_metric_facts_id'), table_name='building_metric_facts')
    op.drop_index(op.f('ix_building_metric_facts_batch_id'), table_name='building_metric_facts')
    op.drop_table('building_metric_facts')
    op.drop_index(op.f('ix_area_measurements_zone_id'), table_name='area_measurements')
    op.drop_index(op.f('ix_area_measurements_source_record_id'), table_name='area_measurements')
    op.drop_index(op.f('ix_area_measurements_room_id'), table_name='area_measurements')
    op.drop_index(op.f('ix_area_measurements_quality_status'), table_name='area_measurements')
    op.drop_index(op.f('ix_area_measurements_id'), table_name='area_measurements')
    op.drop_index(op.f('ix_area_measurements_building_id'), table_name='area_measurements')
    op.drop_index(op.f('ix_area_measurements_area_type'), table_name='area_measurements')
    op.drop_table('area_measurements')
    op.drop_index(op.f('ix_source_records_status'), table_name='source_records')
    op.drop_index(op.f('ix_source_records_id'), table_name='source_records')
    op.drop_index(op.f('ix_source_records_business_object_type'), table_name='source_records')
    op.drop_index(op.f('ix_source_records_business_object_id'), table_name='source_records')
    op.drop_index(op.f('ix_source_records_batch_id'), table_name='source_records')
    op.drop_table('source_records')
    op.drop_index(op.f('ix_import_batches_supersedes_id'), table_name='import_batches')
    op.drop_index(op.f('ix_import_batches_status'), table_name='import_batches')
    op.drop_index(op.f('ix_import_batches_source_id'), table_name='import_batches')
    op.drop_index(op.f('ix_import_batches_period'), table_name='import_batches')
    op.drop_index(op.f('ix_import_batches_id'), table_name='import_batches')
    op.drop_index(op.f('ix_import_batches_as_of_date'), table_name='import_batches')
    op.drop_table('import_batches')
    op.drop_index(op.f('ix_zones_is_active'), table_name='zones')
    op.drop_index(op.f('ix_zones_id'), table_name='zones')
    op.drop_index(op.f('ix_zones_building_id'), table_name='zones')
    op.drop_table('zones')
    op.drop_index(op.f('ix_user_scope_grants_user_id'), table_name='user_scope_grants')
    op.drop_index(op.f('ix_user_scope_grants_scope_type'), table_name='user_scope_grants')
    op.drop_index(op.f('ix_user_scope_grants_portfolio_id'), table_name='user_scope_grants')
    op.drop_index(op.f('ix_user_scope_grants_id'), table_name='user_scope_grants')
    op.drop_index(op.f('ix_user_scope_grants_building_id'), table_name='user_scope_grants')
    op.drop_table('user_scope_grants')
    op.drop_index(op.f('ix_user_capabilities_user_id'), table_name='user_capabilities')
    op.drop_index(op.f('ix_user_capabilities_id'), table_name='user_capabilities')
    op.drop_index(op.f('ix_user_capabilities_capability'), table_name='user_capabilities')
    op.drop_table('user_capabilities')
    op.drop_index(op.f('ix_portfolio_buildings_portfolio_id'), table_name='portfolio_buildings')
    op.drop_index(op.f('ix_portfolio_buildings_building_id'), table_name='portfolio_buildings')
    op.drop_table('portfolio_buildings')
    op.drop_index(op.f('ix_metric_definitions_publication_status'), table_name='metric_definitions')
    op.drop_index(op.f('ix_metric_definitions_id'), table_name='metric_definitions')
    op.drop_index(op.f('ix_metric_definitions_code'), table_name='metric_definitions')
    op.drop_table('metric_definitions')
    op.drop_index(op.f('ix_data_sources_source_type'), table_name='data_sources')
    op.drop_index(op.f('ix_data_sources_is_active'), table_name='data_sources')
    op.drop_index(op.f('ix_data_sources_id'), table_name='data_sources')
    op.drop_index(op.f('ix_data_sources_building_id'), table_name='data_sources')
    op.drop_table('data_sources')
    op.drop_index(op.f('ix_customer_building_profiles_id'), table_name='customer_building_profiles')
    op.drop_index(op.f('ix_customer_building_profiles_customer_id'), table_name='customer_building_profiles')
    op.drop_index(op.f('ix_customer_building_profiles_building_id'), table_name='customer_building_profiles')
    op.drop_table('customer_building_profiles')
    op.drop_index('ux_building_snapshots_current_published', table_name='building_operating_snapshots', sqlite_where=sa.text("publication_status = 'published' AND is_current = 1"))
    op.drop_index(op.f('ix_building_operating_snapshots_validation_status'), table_name='building_operating_snapshots')
    op.drop_index(op.f('ix_building_operating_snapshots_supersedes_id'), table_name='building_operating_snapshots')
    op.drop_index(op.f('ix_building_operating_snapshots_publication_status'), table_name='building_operating_snapshots')
    op.drop_index(op.f('ix_building_operating_snapshots_period_start'), table_name='building_operating_snapshots')
    op.drop_index(op.f('ix_building_operating_snapshots_period_end'), table_name='building_operating_snapshots')
    op.drop_index(op.f('ix_building_operating_snapshots_is_current'), table_name='building_operating_snapshots')
    op.drop_index(op.f('ix_building_operating_snapshots_id'), table_name='building_operating_snapshots')
    op.drop_index(op.f('ix_building_operating_snapshots_building_id'), table_name='building_operating_snapshots')
    op.drop_index(op.f('ix_building_operating_snapshots_as_of_date'), table_name='building_operating_snapshots')
    op.drop_table('building_operating_snapshots')
    op.drop_index(op.f('ix_portfolios_is_active'), table_name='portfolios')
    op.drop_index(op.f('ix_portfolios_id'), table_name='portfolios')
    op.drop_index(op.f('ix_portfolios_code'), table_name='portfolios')
    op.drop_table('portfolios')
    op.drop_index(op.f('ix_buildings_is_active'), table_name='buildings')
    op.drop_index(op.f('ix_buildings_id'), table_name='buildings')
    op.drop_index(op.f('ix_buildings_code'), table_name='buildings')
    op.drop_table('buildings')
    # ### end Alembic commands ###
