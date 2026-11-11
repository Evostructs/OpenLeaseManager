from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class ActionPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)
    expected_revision: int = Field(ge=0)
    owner: str = Field(min_length=1, max_length=100)
    due_on: date
    next_follow_up_on: date | None = None
    status: Literal['open', 'waiting', 'done'] = 'open'
    decision: Literal['undecided', 'renew', 'vacate'] = 'undecided'
    next_action: str = Field(min_length=1, max_length=2000)
    result_note: str = Field(default='', max_length=2000)
    result_contract_id: int | None = Field(default=None, gt=0)
    follow_up_id: int | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def completion_note(self):
        if self.status == 'done' and not self.result_note:
            raise ValueError('完成事项前请填写处理结果；结果核验是独立步骤')
        return self

class RevisionPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1)

class BenefitPayload(RevisionPayload):
    amount_yuan: float = Field(ge=0, le=1e13, allow_inf_nan=False)
    evidence: str = Field(min_length=3, max_length=2000)
    contract_id: int | None = Field(default=None, gt=0)
    expense_id: int | None = Field(default=None, gt=0)

class CreateActionPayload(ActionPayload):
    kind: Literal['expiry', 'measure']
    contract_id: int | None = Field(default=None, gt=0)
    version_id: int | None = Field(default=None, gt=0)
    measure_key: Literal['newLease', 'costSaving'] | None = None
