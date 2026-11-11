from __future__ import annotations

from datetime import date
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PlanningPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class ContractAdjustment(PlanningPayload):
    contract_id: int = Field(gt=0)
    start_shift_months: int = Field(default=0, ge=-24, le=24)
    rent_change_percent: float = Field(default=0, ge=-100, le=500)


class ForecastRequest(PlanningPayload):
    year: int = Field(ge=2000, le=2100)
    closed_through_month: int = Field(default=0, ge=0, le=12)
    base_version_id: int | None = Field(default=None, gt=0)
    contract_adjustments: list[ContractAdjustment] = Field(default_factory=list, max_length=1000)

    @field_validator("contract_adjustments")
    @classmethod
    def unique_contracts(cls, items):
        if len({item.contract_id for item in items}) != len(items):
            raise ValueError("同一合同只能设置一组调整")
        return items


class ExpensePayload(PlanningPayload):
    name: str = Field(min_length=1, max_length=150)
    category: Literal["opex", "capex"]
    amount_yuan: float = Field(ge=0, le=1e13)
    occurrence_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    payment_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    paid_on: date | None = None
    owner: str = Field(default="", max_length=100)
    notes: str = Field(default="", max_length=2000)
    is_active: bool = True
    expected_revision: int | None = Field(default=None, ge=1)

    @field_validator("amount_yuan", mode="before")
    @classmethod
    def amount_is_number(cls, value):
        if value is None or isinstance(value, bool) or value == "":
            raise ValueError("请填写费用金额，零金额可直接填写0")
        return value

    @field_validator("occurrence_month", "payment_month")
    @classmethod
    def valid_month(cls, value):
        parsed = date.fromisoformat(value + "-01")
        if not 2000 <= parsed.year <= 2100:
            raise ValueError("费用月份应在2000年至2100年之间")
        return value


class BudgetMonth(PlanningPayload):
    month: int = Field(ge=1, le=12)
    revenue: float = Field(ge=0, le=1e9)
    operating_cost: float = Field(ge=0, le=1e9)

    @field_validator("revenue", "operating_cost", mode="before")
    @classmethod
    def amount_is_number(cls, value):
        if value is None or isinstance(value, bool) or value == "":
            raise ValueError("请完整填写十二个月预算")
        return value


class SaveVersionRequest(ForecastRequest):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["budget", "forecast", "scenario"]
    expected_revision: int = Field(ge=0)
    budget_version_id: int | None = Field(default=None, gt=0)
    client_state: dict = Field(default_factory=dict)
    budget_months: list[BudgetMonth] | None = Field(default=None, max_length=12)

    @field_validator("client_state")
    @classmethod
    def bounded_state(cls, value):
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValueError("方案参数格式无效") from exc
        if len(encoded.encode("utf-8")) > 512_000:
            raise ValueError("方案参数过大")
        return value

    @model_validator(mode="after")
    def complete_budget(self):
        if self.kind == "budget":
            if self.budget_months is None or {row.month for row in self.budget_months} != set(range(1, 13)):
                raise ValueError("年度预算必须包含不重复的十二个月")
        elif self.budget_months is not None:
            raise ValueError("只有预算版本可以设置月度批准预算")
        return self


class AdoptVersionRequest(PlanningPayload):
    expected_revision: int = Field(ge=0)


class CompareRequest(ForecastRequest):
    baseline_version_id: int = Field(gt=0)
    current_version_id: int | None = Field(default=None, gt=0)
    metric: Literal["ebitda", "revenue", "operating_cost", "net_cash_flow"] = "ebitda"
