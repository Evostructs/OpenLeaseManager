"""Operational finance ledger. Existing rent receipts remain the rent subledger."""
from sqlalchemy import Column, Integer, String, Text, Date, DateTime, Boolean, ForeignKey, UniqueConstraint, CheckConstraint, Index, text
from app.database import Base
from app.models import MONEY
from app.core.time import now_in_timezone


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
