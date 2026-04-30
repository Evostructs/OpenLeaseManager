from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class LoginForm(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = ""
    role: Optional[str] = "editor"


class ChangePassword(BaseModel):
    old_password: str
    new_password: str


class ResetPassword(BaseModel):
    user_id: int
    new_password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: str
    created_at: Optional[datetime] = None


class ContractCreate(BaseModel):
    contract_no: str
    tower: Optional[str] = ""
    room_no: Optional[str] = ""
    tenant_name: Optional[str] = ""
    area: Optional[float] = 0
    unit_price: Optional[float] = 0
    lease_type: Optional[str] = ""
    free_period: Optional[int] = 0
    monthly_rent: Optional[float] = 0
    broker_fee: Optional[float] = 0
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    disposal: Optional[str] = ""
    contact_person: Optional[str] = ""
    contact_phone: Optional[str] = ""
    contact_email: Optional[str] = ""
    deposit: Optional[float] = 0
    deposit_status: Optional[str] = ""
    property_fee_rate: Optional[float] = 0
    prev_contract_no: Optional[str] = ""
    notes: Optional[str] = ""


class ContractUpdate(BaseModel):
    contract_no: Optional[str] = None
    tower: Optional[str] = None
    room_no: Optional[str] = None
    tenant_name: Optional[str] = None
    area: Optional[float] = None
    unit_price: Optional[float] = None
    lease_type: Optional[str] = None
    free_period: Optional[int] = None
    monthly_rent: Optional[float] = None
    broker_fee: Optional[float] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    disposal: Optional[str] = None
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    deposit: Optional[float] = None
    deposit_status: Optional[str] = None
    property_fee_rate: Optional[float] = None
    prev_contract_no: Optional[str] = None
    last_updated: Optional[str] = None
    notes: Optional[str] = None


class ContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_no: str
    tower: str
    room_no: str
    tenant_name: str
    area: float
    unit_price: float
    lease_type: str
    free_period: int
    monthly_rent: float
    broker_fee: float
    net_rent: float
    start_date: Optional[date]
    end_date: Optional[date]
    status: str
    days_to_expiry: Optional[int]
    disposal: str
    contact_person: str
    contact_phone: str
    contact_email: str
    deposit: float
    deposit_status: str
    property_fee_rate: float
    monthly_property_fee: float
    prev_contract_no: str
    notes: str
    pdf_path: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


class ContractListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ContractOut]


class LogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: Optional[int] = None
    action: str
    changes: str
    operator: str
    timestamp: Optional[datetime]


class RoomPayload(BaseModel):
    tower: str = ""
    floor: str = ""
    room_no: str
    room_type: str = "办公"
    area: float = 0
    is_leasable: bool = True
    notes: str = ""


class RoomOut(BaseModel):
    id: int
    tower: str
    floor: str
    room_no: str
    room_type: str
    area: float
    is_leasable: bool
    notes: str
    occupied: bool = False
    contract: Optional[dict] = None


class BackupOut(BaseModel):
    filename: str
    size: int
    created: str


class FeishuConfigPayload(BaseModel):
    webhook_url: str = Field(default="")


class IntegrationConnectionPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider: str
    direction: str = Field(default="bidirectional")
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    secret: Optional[dict[str, Any]] = None


class IntegrationConnectionOut(BaseModel):
    id: int
    name: str
    provider: str
    provider_label: str
    direction: str
    direction_label: str
    enabled: bool
    config: dict[str, Any]
    secret_configured: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class IntegrationEventOut(BaseModel):
    id: int
    connection_id: Optional[int] = None
    connection_name: str = ""
    event_type: str
    resource_type: str
    resource_id: Optional[int] = None
    status: str
    attempts: int
    last_error: str
    payload: dict[str, Any]
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
