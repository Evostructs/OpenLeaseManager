"""Canonical lease and saved-plan execution records (additive)."""
from alembic import op
import sqlalchemy as sa
revision = '20260928_0031'
down_revision = '20260928_0030'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('execution_actions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('building_id', sa.Integer(), sa.ForeignKey('buildings.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('source_key', sa.String(100), nullable=False),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('contract_id', sa.Integer(), sa.ForeignKey('contracts.id', ondelete='RESTRICT')),
        sa.Column('version_id', sa.Integer(), sa.ForeignKey('financial_plan_versions.id', ondelete='RESTRICT')),
        sa.Column('measure_key', sa.String(20)),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('source_json', sa.Text(), nullable=False),
        sa.Column('owner', sa.String(100), nullable=False),
        sa.Column('due_on', sa.Date(), nullable=False),
        sa.Column('next_follow_up_on', sa.Date()),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('decision', sa.String(12), nullable=False),
        sa.Column('next_action', sa.Text(), nullable=False),
        sa.Column('result_note', sa.Text(), nullable=False),
        sa.Column('result_contract_id', sa.Integer(), sa.ForeignKey('contracts.id', ondelete='RESTRICT')),
        sa.Column('follow_up_id', sa.Integer(), sa.ForeignKey('customer_follow_ups.id', ondelete='RESTRICT')),
        sa.Column('verification_json', sa.Text(), nullable=False),
        sa.Column('benefit_json', sa.Text(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.String(100), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('building_id','source_key',name='uq_execution_action_source'),
        sa.ForeignKeyConstraint(['building_id','contract_id'],['contracts.building_id','contracts.id'],name='fk_execution_scope_contract',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['building_id','result_contract_id'],['contracts.building_id','contracts.id'],name='fk_execution_scope_result',ondelete='RESTRICT'),
        sa.CheckConstraint("kind IN ('expiry','measure')",name='ck_execution_action_kind'),
        sa.CheckConstraint("status IN ('open','waiting','done')",name='ck_execution_action_status'),
        sa.CheckConstraint("decision IN ('undecided','renew','vacate')",name='ck_execution_action_decision'),
        sa.CheckConstraint('revision >= 1',name='ck_execution_action_revision'),
    )
    op.create_index('ix_execution_actions_scope_due','execution_actions',['building_id','status','due_on'])

def downgrade():
    if op.get_bind().execute(sa.text('SELECT COUNT(*) FROM execution_actions')).scalar_one():
        raise RuntimeError('执行与核验记录不可自动删除；回退应用时保留此表。')
    op.drop_table('execution_actions')
