"""Operational finance ledger. Existing rent receipts remain the rent subledger."""
from sqlalchemy import Column, Integer, String, Text, Date, DateTime, Boolean, ForeignKey, UniqueConstraint, CheckConstraint, Index, text
from sqlalchemy.orm import declarative_base
from sqlalchemy import Numeric, Table
from datetime import datetime
Base = declarative_base()
MONEY = Numeric(18, 2)
now_in_timezone = datetime.now
for reference in ("buildings", "contracts", "financial_expenses", "contract_deposits", "receivable_payments"):
    Table(reference, Base.metadata, Column("id", Integer, primary_key=True))


class FinanceCash(Base):
    __tablename__ = 'finance_cash'
    __table_args__ = (UniqueConstraint('building_id', 'entry_key', name='uq_finance_cash_key'),
                      CheckConstraint("direction IN ('in','out')", name='ck_finance_cash_direction'),
                      CheckConstraint('amount > 0', name='ck_finance_cash_amount'))
    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey('buildings.id'), nullable=False, index=True)
    entry_key = Column(String(100), nullable=False)
    request_hash = Column(String(64), nullable=False)
    direction = Column(String(3), nullable=False)
    occurred_on = Column(Date, nullable=False, index=True)
    amount = Column(MONEY, nullable=False)
    counterparty = Column(String(150), nullable=False)
    reference = Column(String(150), nullable=False, default='')
    note = Column(Text, nullable=False, default='')
    origin = Column(String(20), nullable=False, default='manual')
    revision = Column(Integer, nullable=False, default=1)
    voided = Column(Boolean, nullable=False, default=False)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)


class FinanceAllocation(Base):
    __tablename__ = 'finance_allocations'
    __table_args__ = (Index('uq_finance_active_payment', 'payment_id', unique=True, sqlite_where=text('voided = 0')),
                      UniqueConstraint('building_id', 'entry_key', name='uq_finance_allocation_key'),
                      CheckConstraint("kind IN ('rent','property_fee','deposit','expense')", name='ck_finance_allocation_kind'),
                      CheckConstraint('amount > 0', name='ck_finance_allocation_amount'))
    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey('buildings.id'), nullable=False, index=True)
    cash_id = Column(Integer, ForeignKey('finance_cash.id', ondelete='RESTRICT'), nullable=False, index=True)
    entry_key = Column(String(100), nullable=False)
    request_hash = Column(String(64), nullable=False)
    kind = Column(String(20), nullable=False)
    amount = Column(MONEY, nullable=False)
    period_month = Column(String(7), nullable=False)
    contract_id = Column(Integer, ForeignKey('contracts.id', ondelete='RESTRICT'), index=True)
    expense_id = Column(Integer, ForeignKey('financial_expenses.id', ondelete='RESTRICT'), index=True)
    deposit_id = Column(Integer, ForeignKey('contract_deposits.id', ondelete='RESTRICT'))
    payment_id = Column(Integer, ForeignKey('receivable_payments.id', ondelete='RESTRICT'))
    reversal_payment_id = Column(Integer, ForeignKey('receivable_payments.id', ondelete='RESTRICT'))
    voided = Column(Boolean, nullable=False, default=False)
    note = Column(Text, nullable=False, default='')
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)


class FinanceBankLine(Base):
    __tablename__ = 'finance_bank_lines'
    __table_args__ = (UniqueConstraint('building_id', 'import_key', name='uq_finance_bank_import'),
                      CheckConstraint("direction IN ('in','out')", name='ck_finance_bank_direction'),
                      CheckConstraint('amount > 0', name='ck_finance_bank_amount'))
    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey('buildings.id'), nullable=False, index=True)
    import_key = Column(String(150), nullable=False)
    request_hash = Column(String(64), nullable=False)
    occurred_on = Column(Date, nullable=False)
    direction = Column(String(3), nullable=False)
    amount = Column(MONEY, nullable=False)
    counterparty = Column(String(150), nullable=False)
    reference = Column(String(150), nullable=False, default='')
    account_label = Column(String(100), nullable=False, default='')
    origin = Column(String(20), nullable=False, default='import')
    revision = Column(Integer, nullable=False, default=1)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)


class FinanceBankMatch(Base):
    __tablename__ = 'finance_bank_matches'
    __table_args__ = (UniqueConstraint('building_id', 'entry_key', name='uq_finance_bank_match_key'),
                      CheckConstraint('amount > 0', name='ck_finance_match_amount'))
    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey('buildings.id'), nullable=False)
    bank_id = Column(Integer, ForeignKey('finance_bank_lines.id'), nullable=False, index=True)
    cash_id = Column(Integer, ForeignKey('finance_cash.id'), nullable=False, index=True)
    entry_key = Column(String(100), nullable=False)
    request_hash = Column(String(64), nullable=False)
    amount = Column(MONEY, nullable=False)
    note = Column(Text, nullable=False, default='')
    voided = Column(Boolean, nullable=False, default=False)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)


class FinanceExpenseDetail(Base):
    __tablename__ = 'finance_expense_details'
    expense_id = Column(Integer, ForeignKey('financial_expenses.id'), primary_key=True)
    supplier = Column(String(150), nullable=False, default='')
    cost_category = Column(String(80), nullable=False, default='')
    cost_center = Column(String(100), nullable=False, default='')
    service_from = Column(String(7), nullable=False)
    service_to = Column(String(7), nullable=False)
    evidence = Column(Text, nullable=False, default='')
    demo_key = Column(String(100), unique=True)
    creation_key = Column(String(150), unique=True)
    creation_hash = Column(String(64))


class FinancePeriod(Base):
    __tablename__ = 'finance_periods'
    __table_args__ = (UniqueConstraint('building_id', 'period_month', name='uq_finance_period'),
                      CheckConstraint("status IN ('open','submitted','closed')", name='ck_finance_period_status'))
    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey('buildings.id'), nullable=False, index=True)
    period_month = Column(String(7), nullable=False)
    status = Column(String(15), nullable=False, default='open')
    revision = Column(Integer, nullable=False, default=1)
    confirmations_json = Column(Text, nullable=False, default='{}')
    source_digest = Column(String(64), nullable=False, default='')
    snapshot_json = Column(Text, nullable=False, default='{}')
    submitted_by = Column(String(100), nullable=False, default='')
    reviewed_by = Column(String(100), nullable=False, default='')
    updated_at = Column(DateTime, nullable=False, default=now_in_timezone)


class FinancePeriodEvent(Base):
    __tablename__ = 'finance_period_events'
    id = Column(Integer, primary_key=True)
    period_id = Column(Integer, ForeignKey('finance_periods.id'), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    action = Column(String(20), nullable=False)
    actor = Column(String(100), nullable=False)
    reason = Column(Text, nullable=False)
    snapshot_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)

from alembic import op
import sqlalchemy as sa
revision = '20260928_0032'
down_revision = '20260928_0031'
branch_labels = None
depends_on = None
TABLES = [FinanceCash.__table__, FinanceAllocation.__table__, FinanceBankLine.__table__, FinanceBankMatch.__table__, FinanceExpenseDetail.__table__, FinancePeriod.__table__, FinancePeriodEvent.__table__]


def guard_sql():
    guards = {}
    def add(name, table, event, condition, error='FINANCE_PERIOD_CLOSED'):
        guards[name] = f"CREATE TRIGGER {name} BEFORE {event} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT, '{error}'); END"
    def closed(bid, condition):
        return f"EXISTS (SELECT 1 FROM finance_periods p WHERE p.building_id={bid} AND p.status='closed' AND ({condition}))"
    def contract_bid(ref): return f'(SELECT building_id FROM contracts WHERE id={ref}.contract_id)'
    direct = {
      'finance_cash': lambda r: closed(f'{r}.building_id', f"p.period_month=substr({r}.occurred_on,1,7)"),
      'finance_bank_lines': lambda r: closed(f'{r}.building_id', f"p.period_month=substr({r}.occurred_on,1,7)"),
      'finance_allocations': lambda r: closed(f'{r}.building_id', f"p.period_month=(SELECT substr(occurred_on,1,7) FROM finance_cash WHERE id={r}.cash_id)"),
      'finance_bank_matches': lambda r: closed(f'{r}.building_id', f"p.period_month=(SELECT substr(occurred_on,1,7) FROM finance_cash WHERE id={r}.cash_id) OR p.period_month=(SELECT substr(occurred_on,1,7) FROM finance_bank_lines WHERE id={r}.bank_id)"),
      'receivable_payments': lambda r: closed(contract_bid(r), f"p.period_month=coalesce(substr({r}.payment_date,1,7),{r}.period_month)"),
      'receivable_records': lambda r: closed(contract_bid(r), f"p.period_month=coalesce(substr({r}.received_date,1,7),{r}.period_month)"),
      'receivable_statements': lambda r: closed(contract_bid(r), f"p.period_month={r}.period_month"),
      'financial_expenses': lambda r: closed(f'{r}.building_id', f"p.period_month={r}.occurrence_month OR p.period_month={r}.payment_month OR p.period_month=substr({r}.paid_on,1,7) OR EXISTS (SELECT 1 FROM finance_expense_details d WHERE d.expense_id={r}.id AND p.period_month BETWEEN d.service_from AND d.service_to)"),
      'finance_expense_details': lambda r: closed(f'(SELECT building_id FROM financial_expenses WHERE id={r}.expense_id)', f'p.period_month BETWEEN {r}.service_from AND {r}.service_to'),
      'contract_deposits': lambda r: closed(contract_bid(r), f"p.period_month=substr({r}.received_date,1,7) OR p.period_month=substr({r}.refund_date,1,7) OR EXISTS (SELECT 1 FROM finance_allocations a WHERE a.deposit_id={r}.id AND a.period_month=p.period_month)"),
      'contracts': lambda r: closed(f'{r}.building_id', f"p.period_month BETWEEN substr({r}.start_date,1,7) AND substr({r}.end_date,1,7)"),
      'rent_terms': lambda r: closed(contract_bid(r), f"EXISTS (SELECT 1 FROM contracts c WHERE c.id={r}.contract_id AND p.period_month BETWEEN substr(c.start_date,1,7) AND substr(c.end_date,1,7))"),
      'contract_rooms': lambda r: closed(contract_bid(r), f"EXISTS (SELECT 1 FROM contracts c WHERE c.id={r}.contract_id AND p.period_month BETWEEN substr(c.start_date,1,7) AND substr(c.end_date,1,7))"),
    }
    for table, check in direct.items():
        for event, refs in [('INSERT',['NEW']),('UPDATE',['OLD','NEW']),('DELETE',['OLD'])]:
            condition=' OR '.join(check(r) for r in refs)
            if table=='contracts' and event=='UPDATE':
                fields=['building_id','start_date','end_date','monthly_rent','area','unit_price','free_period','property_fee_rate','property_fee_free','is_deleted','disposal']
                condition='('+condition+') AND ('+' OR '.join(f'OLD.{f} IS NOT NEW.{f}' for f in fields)+')'
            if table=='receivable_records':
                if event=='UPDATE':
                    condition='('+condition+') AND ('+' OR '.join(f'OLD.{f} IS NOT NEW.{f}' for f in ['received_amount','received_date','contract_id','period_month'])+')'
                else:
                    condition='('+condition+') AND '+('coalesce(NEW.received_amount,0)<>0' if event=='INSERT' else 'coalesce(OLD.received_amount,0)<>0')
            if table=='financial_expenses' and event=='UPDATE':
                fields=['building_id','name','category','amount_yuan','occurrence_month','payment_month','paid_on','owner','notes','is_active']
                condition='('+condition+') AND ('+' OR '.join(f'OLD.{f} IS NOT NEW.{f}' for f in fields)+')'
            if table=='contract_deposits' and event=='UPDATE':
                condition=' OR '.join(closed(contract_bid(r), f"(p.period_month=substr({r}.received_date,1,7) AND (OLD.received_amount IS NOT NEW.received_amount OR OLD.received_date IS NOT NEW.received_date)) OR (p.period_month=substr({r}.refund_date,1,7) AND (OLD.refunded_amount IS NOT NEW.refunded_amount OR OLD.refund_date IS NOT NEW.refund_date)) OR (p.period_month=substr({r}.received_date,1,7) AND (OLD.offset_amount IS NOT NEW.offset_amount OR OLD.contract_id IS NOT NEW.contract_id))") for r in refs)
            add('trg_finance_closed_'+table+'_'+event.lower(),table,event,condition)
    for event in ['UPDATE','DELETE']:
        add('trg_finance_event_'+event.lower(),'finance_period_events',event,'1','FINANCE_IMMUTABLE')
        add('trg_finance_payment_'+event.lower(),'receivable_payments',event,"EXISTS (SELECT 1 FROM finance_allocations a WHERE a.payment_id=OLD.id OR a.reversal_payment_id=OLD.id)",'FINANCE_LINKED_PAYMENT')
    add('trg_finance_expense_paid','financial_expenses','UPDATE',"EXISTS (SELECT 1 FROM finance_allocations a WHERE a.expense_id=OLD.id AND a.voided=0) AND (NEW.paid_on IS NOT NULL OR NEW.is_active=0 OR ROUND(NEW.amount_yuan*100)<(SELECT SUM(ROUND(a.amount*100)) FROM finance_allocations a WHERE a.expense_id=OLD.id AND a.voided=0))",'FINANCE_LINKED_EXPENSE')
    return guards


def upgrade():
    bind=op.get_bind()
    for table in TABLES: table.create(bind)
    for sql in guard_sql().values(): bind.exec_driver_sql(sql)


def downgrade():
    bind=op.get_bind()
    if any(bind.execute(sa.text(f'SELECT COUNT(*) FROM {t.name}')).scalar_one() for t in TABLES):
        raise RuntimeError('资金、核对和关账记录不可自动删除；回退应用时保留此迁移。')
    for name in guard_sql(): bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS {name}')
    for table in reversed(TABLES): table.drop(bind)
