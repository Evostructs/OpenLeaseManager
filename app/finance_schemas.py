from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)


class CashPayload(Payload):
    entry_key: str = Field(min_length=8, max_length=100)
    direction: Literal['in', 'out']
    occurred_on: date
    amount: Decimal = Field(gt=0, le=1e12, decimal_places=2)
    counterparty: str = Field(min_length=1, max_length=150)
    reference: str = Field(default='', max_length=150)
    note: str = Field(default='', max_length=2000)


class AllocationPayload(Payload):
    entry_key: str = Field(min_length=8, max_length=100)
    expected_revision: int = Field(ge=1)
    kind: Literal['rent', 'property_fee', 'deposit', 'expense']
    amount: Decimal = Field(gt=0, le=1e12, decimal_places=2)
    period_month: str = Field(pattern=r'^(20\d{2}|2100)-(0[1-9]|1[0-2])$')
    contract_id: int | None = Field(default=None, gt=0)
    expense_id: int | None = Field(default=None, gt=0)
    deposit_id: int | None = Field(default=None, gt=0)
    existing_payment_id: int | None = Field(default=None, gt=0)
    note: str = Field(default='', max_length=2000)


class BankLinePayload(Payload):
    import_key: str = Field(min_length=1, max_length=150)
    occurred_on: date
    direction: Literal['in', 'out']
    amount: Decimal = Field(gt=0, le=1e12, decimal_places=2)
    counterparty: str = Field(min_length=1, max_length=150)
    reference: str = Field(default='', max_length=150)
    account_label: str = Field(default='', max_length=100)


class BankImportPayload(Payload):
    rows: list[BankLinePayload] = Field(min_length=1, max_length=500)


class MatchPayload(Payload):
    entry_key: str = Field(min_length=8, max_length=100)
    expected_revision: int = Field(ge=1)
    cash_revision: int = Field(ge=1)
    cash_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, le=1e12, decimal_places=2)
    note: str = Field(default='', max_length=2000)


class ReasonPayload(Payload):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=2000)


class PeriodPayload(ReasonPayload):
    action: Literal['save', 'submit', 'close', 'reopen', 'return']
    confirmations: dict[str, Literal['missing', 'provided', 'zero']] = Field(default_factory=dict)

    @field_validator('confirmations')
    @classmethod
    def domains(cls, value):
        if set(value) - {'income', 'receipts', 'expenses', 'payments'}:
            raise ValueError('未知资料类别')
        return value


class ExpenseDetailPayload(Payload):
    expected_revision: int = Field(ge=1)
    supplier: str = Field(min_length=1, max_length=150)
    cost_category: str = Field(min_length=1, max_length=80)
    cost_center: str = Field(default='', max_length=100)
    service_from: str = Field(pattern=r'^(20\d{2}|2100)-(0[1-9]|1[0-2])$')
    service_to: str = Field(pattern=r'^(20\d{2}|2100)-(0[1-9]|1[0-2])$')
    evidence: str = Field(min_length=3, max_length=2000)

    @model_validator(mode='after')
    def range(self):
        a, b = date.fromisoformat(self.service_from+'-01'), date.fromisoformat(self.service_to+'-01')
        if a > b or (b.year-a.year)*12+b.month-a.month > 119:
            raise ValueError('费用服务期间应为1至120个月')
        return self

from app.financial_planning_schemas import ExpensePayload
class ExpenseCreatePayload(Payload):
    entry_key: str = Field(min_length=8,max_length=100)
    expense: ExpensePayload
    detail: ExpenseDetailPayload
