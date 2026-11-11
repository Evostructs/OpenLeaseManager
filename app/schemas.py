from __future__ import annotations

from datetime import date, datetime
import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# 联系方式轻量校验：录错的电话/邮箱会让催收链路失效，
# 这里只拦明显非法格式，允许留空
_PHONE_PATTERN = re.compile(r"^[0-9+\-\s()（）转#]{6,30}$")
_EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def _validate_phone_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    text = str(value).strip()
    if text and not _PHONE_PATTERN.match(text):
        raise ValueError("联系电话格式不正确（6-30 位，仅数字、+、-、空格、括号、转、#）")
    return text


def _validate_email_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    text = str(value).strip()
    if text and (len(text) > 100 or not _EMAIL_PATTERN.match(text)):
        raise ValueError("联系邮箱格式不正确")
    return text


class LoginForm(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = ""
    role: Optional[str] = "editor"


class CreateUserForm(LoginForm):
    initial_scope: Literal["legacy_demo_a", "none"] = "legacy_demo_a"


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


class AttachmentUpdate(BaseModel):
    category: Optional[str] = None
    notes: Optional[str] = None


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: str
    entity_id: int
    file_name: str
    content_type: str = ""
    file_size: int = 0
    category: str = "其他"
    notes: str = ""
    uploaded_by: str = ""
    created_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None


class AttachmentSummary(BaseModel):
    total: int = 0
    categories: dict[str, int] = Field(default_factory=dict)
    missing_required: list[str] = Field(default_factory=list)


class AttachmentCompletenessTaskOut(BaseModel):
    id: str
    target_type: str
    target_id: int
    contract_id: Optional[int] = None
    customer_id: Optional[int] = None
    contract_no: str = ""
    customer_name: str = ""
    room_no: str = ""
    tower: str = ""
    title: str
    reason: str
    severity: str = "medium"
    priority: str = "medium"
    missing_category: str
    suggested_action: str
    action_target: dict = Field(default_factory=dict)


class AttachmentCompletenessTasksResponse(BaseModel):
    total: int = 0
    summary: dict = Field(default_factory=dict)
    items: list[AttachmentCompletenessTaskOut] = Field(default_factory=list)


class OperationsMetricV2(BaseModel):
    key: str
    label: str
    value: str
    raw_value: float | int | str | None = None
    unit: str = ""
    helper: str = ""
    tone: str = "info"
    progress: Optional[float] = None
    intent_key: str = ""


class OperationsSignalV2(BaseModel):
    key: str
    label: str
    tone: str = "info"
    items: list[dict] = Field(default_factory=list)


class OperationsLaneV2(BaseModel):
    key: str
    label: str
    tone: str = "info"
    count: int = 0
    value: str = ""
    items: list[dict] = Field(default_factory=list)


class OperationsActionV2(BaseModel):
    id: str
    type: str
    priority: str = "P2"
    tone: str = "info"
    title: str
    subtitle: str = ""
    value: str = ""
    signals: list[dict] = Field(default_factory=list)
    target: dict = Field(default_factory=dict)
    primary_action: dict = Field(default_factory=dict)
    secondary_actions: list[dict] = Field(default_factory=list)
    intent_key: str = ""


class OperationsDashboardV2Response(BaseModel):
    contract_version: str
    date: str
    month: str
    facts: dict = Field(default_factory=dict)
    metrics: list[OperationsMetricV2] = Field(default_factory=list)
    signals: list[OperationsSignalV2] = Field(default_factory=list)
    lanes: list[OperationsLaneV2] = Field(default_factory=list)
    next_actions: list[OperationsActionV2] = Field(default_factory=list)
    drilldown_intents: dict = Field(default_factory=dict)


class ContractRoomPayload(BaseModel):
    id: Optional[int] = None
    room_id: Optional[int] = None
    tower: Optional[str] = ""
    room_no: str
    area: Optional[float] = 0
    unit_price: Optional[float] = 0
    monthly_rent: Optional[float] = 0
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    notes: Optional[str] = ""


class ContractRoomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[int] = None
    room_id: Optional[int] = None
    tower: str = ""
    room_no: str = ""
    area: float = 0
    unit_price: float = 0
    monthly_rent: float = 0
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    notes: str = ""


class RentTermPayload(BaseModel):
    contract_room_id: Optional[int] = None
    room_no: Optional[str] = ""
    rule_type: str
    start_date: date
    end_date: date
    area: Optional[float] = None
    unit_price: Optional[float] = None
    monthly_amount: Optional[float] = None
    adjustment_type: Optional[str] = None
    adjustment_value: Optional[float] = None
    frequency: Optional[str] = None
    notes: Optional[str] = ""


class RentTermOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    contract_room_id: Optional[int] = None
    room_no: Optional[str] = ""
    rule_type: str
    start_date: date
    end_date: date
    area: Optional[float] = None
    unit_price: Optional[float] = None
    monthly_amount: Optional[float] = None
    adjustment_type: Optional[str] = None
    adjustment_value: Optional[float] = None
    frequency: Optional[str] = None
    notes: str = ""


class DepositBase(BaseModel):
    deposit_type: Optional[str] = "租赁押金"
    expected_amount: Optional[float] = 0
    due_date: Optional[date] = None
    notes: Optional[str] = ""


class DepositCreate(DepositBase):
    pass


class DepositUpdate(BaseModel):
    deposit_type: Optional[str] = None
    expected_amount: Optional[float] = None
    received_amount: Optional[float] = None
    refunded_amount: Optional[float] = None
    offset_amount: Optional[float] = None
    due_date: Optional[date] = None
    received_date: Optional[date] = None
    refund_date: Optional[date] = None
    notes: Optional[str] = None


class DepositActionPayload(BaseModel):
    amount: float
    action_date: Optional[date] = None
    notes: Optional[str] = ""


class DepositOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    customer_id: Optional[int] = None
    deposit_type: str = "租赁押金"
    expected_amount: float = 0
    received_amount: float = 0
    refunded_amount: float = 0
    offset_amount: float = 0
    unpaid_amount: float = 0
    available_amount: float = 0
    status: str = "unpaid"
    due_date: Optional[date] = None
    received_date: Optional[date] = None
    refund_date: Optional[date] = None
    notes: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DepositSummary(BaseModel):
    expected_amount: float = 0
    received_amount: float = 0
    refunded_amount: float = 0
    offset_amount: float = 0
    unpaid_amount: float = 0
    available_amount: float = 0
    insufficient_amount: float = 0
    count: int = 0
    unpaid_count: int = 0
    available_count: int = 0


class CustomerBuildingProfileOut(BaseModel):
    building_code: str
    local_contact: str = ""
    local_notes: str = ""
    owner: str = ""
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    has_profile: bool = False


class CustomerSummary(BaseModel):
    id: int
    name: str
    short_name: str = ""
    credit_code: str = ""
    customer_type: str = ""
    industry: str = ""
    contact_name: str = ""
    contact_phone: str = ""
    contact_email: str = ""
    status: str = "active"
    local_contact: str = ""
    local_notes: str = ""
    owner: str = ""
    building_profile: Optional[CustomerBuildingProfileOut] = None
    building_profiles: list[CustomerBuildingProfileOut] = Field(default_factory=list)
    shared_master: dict = Field(default_factory=dict)


class CustomerBase(BaseModel):
    name: str
    short_name: Optional[str] = ""
    credit_code: Optional[str] = ""
    customer_type: Optional[str] = ""
    industry: Optional[str] = ""
    contact_name: Optional[str] = ""
    contact_phone: Optional[str] = ""
    contact_email: Optional[str] = ""
    address: Optional[str] = ""
    notes: Optional[str] = ""
    status: Optional[str] = "active"
    local_contact: Optional[str] = ""
    local_notes: Optional[str] = ""
    owner: Optional[str] = ""

    @field_validator("contact_phone")
    @classmethod
    def _check_contact_phone(cls, value):
        return _validate_phone_text(value)

    @field_validator("contact_email")
    @classmethod
    def _check_contact_email(cls, value):
        return _validate_email_text(value)


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    short_name: Optional[str] = None
    credit_code: Optional[str] = None
    customer_type: Optional[str] = None
    industry: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    local_contact: Optional[str] = None
    local_notes: Optional[str] = None
    owner: Optional[str] = None

    @field_validator("contact_phone")
    @classmethod
    def _check_contact_phone(cls, value):
        return _validate_phone_text(value)

    @field_validator("contact_email")
    @classmethod
    def _check_contact_email(cls, value):
        return _validate_email_text(value)



class CustomerFollowUpCreate(BaseModel):
    contract_id: Optional[int] = None
    follow_up_type: Optional[str] = "电话"
    occurred_at: Optional[datetime] = None
    content: str
    next_action: Optional[str] = ""
    next_follow_up_date: Optional[date] = None
    owner: Optional[str] = ""
    status: Optional[str] = "open"


class CustomerFollowUpUpdate(BaseModel):
    contract_id: Optional[int] = None
    follow_up_type: Optional[str] = None
    occurred_at: Optional[datetime] = None
    content: Optional[str] = None
    next_action: Optional[str] = None
    next_follow_up_date: Optional[date] = None
    owner: Optional[str] = None
    status: Optional[str] = None


class CustomerFollowUpOut(BaseModel):
    id: int
    customer_id: int
    contract_id: Optional[int] = None
    contract_no: str = ""
    follow_up_type: str = ""
    occurred_at: Optional[datetime] = None
    content: str = ""
    next_action: str = ""
    next_follow_up_date: Optional[date] = None
    owner: str = ""
    status: str = "open"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CustomerOperatingSummary(BaseModel):
    current_leased_area: float = 0
    current_monthly_rent: float = 0
    current_month: str = ""
    current_month_receivable: float = 0
    unpaid_balance: float = 0
    overdue_balance: float = 0
    contract_count: int = 0
    nearest_expiring_contract: Optional[dict] = None
    nearest_expiry_days: Optional[int] = None
    last_follow_up_time: Optional[datetime] = None
    next_follow_up_date: Optional[date] = None
    deposit_expected_amount: float = 0
    deposit_received_amount: float = 0
    deposit_unpaid_amount: float = 0
    deposit_refunded_amount: float = 0
    deposit_offset_amount: float = 0
    deposit_available_amount: float = 0
    deposit_refund_pending_amount: float = 0
    deposit_insufficient_amount: float = 0
    deposit_unpaid_count: int = 0
    deposit_refund_pending_count: int = 0
    deposit_insufficient_count: int = 0
    action_cues: list[dict] = Field(default_factory=list)


class CustomerOut(CustomerSummary):
    address: str = ""
    notes: str = ""
    contract_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CustomerDetailOut(CustomerOut):
    contracts: list[dict] = Field(default_factory=list)
    operating_summary: CustomerOperatingSummary = Field(default_factory=CustomerOperatingSummary)
    follow_ups: list[CustomerFollowUpOut] = Field(default_factory=list)
    attachments: list[AttachmentOut] = Field(default_factory=list)
    attachment_summary: AttachmentSummary = Field(default_factory=AttachmentSummary)
    risks: list[dict] = Field(default_factory=list)


class AiCustomerRequest(BaseModel):
    customer_id: int


class AiRiskExplanationRequest(BaseModel):
    id: Optional[str] = None
    stable_key: Optional[str] = None
    risk_type: Optional[str] = None
    target_type: Optional[str] = None
    target_id: Optional[int] = None
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    customer_id: Optional[int] = None
    contract_id: Optional[int] = None


class AiAssistResponse(BaseModel):
    summary: str = ""
    suggestion: str = ""
    explanation: str = ""
    highlights: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    generated_at: str = ""
    model: str = ""
    provider_label: str = ""


class ContractCreate(BaseModel):
    contract_no: str
    customer_id: Optional[int] = None
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
    internal_contact_person: Optional[str] = ""
    internal_contact_feishu_id: Optional[str] = ""
    deposit: Optional[float] = 0
    deposit_status: Optional[str] = ""
    property_fee_rate: Optional[float] = 0
    prev_contract_no: Optional[str] = ""
    payment_due_day: Optional[int] = None
    notes: Optional[str] = ""
    room_items: Optional[list[ContractRoomPayload]] = None

    @field_validator("contact_phone")
    @classmethod
    def _check_contact_phone(cls, value):
        return _validate_phone_text(value)

    @field_validator("contact_email")
    @classmethod
    def _check_contact_email(cls, value):
        return _validate_email_text(value)

    @field_validator("payment_due_day")
    @classmethod
    def _check_payment_due_day(cls, value):
        if value in (None, ""):
            return None
        day = int(value)
        if not 1 <= day <= 31:
            raise ValueError("付款日应为 1-31 之间的日期（留空表示月末到期）")
        return day



class ContractUpdate(BaseModel):
    contract_no: Optional[str] = None
    customer_id: Optional[int] = None
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
    internal_contact_person: Optional[str] = None
    internal_contact_feishu_id: Optional[str] = None
    deposit: Optional[float] = None
    deposit_status: Optional[str] = None
    property_fee_rate: Optional[float] = None
    prev_contract_no: Optional[str] = None
    payment_due_day: Optional[int] = None
    last_updated: Optional[str] = None
    notes: Optional[str] = None
    room_items: Optional[list[ContractRoomPayload]] = None

    @field_validator("contact_phone")
    @classmethod
    def _check_contact_phone(cls, value):
        return _validate_phone_text(value)

    @field_validator("contact_email")
    @classmethod
    def _check_contact_email(cls, value):
        return _validate_email_text(value)

    @field_validator("payment_due_day")
    @classmethod
    def _check_payment_due_day(cls, value):
        if value in (None, ""):
            return None
        day = int(value)
        if not 1 <= day <= 31:
            raise ValueError("付款日应为 1-31 之间的日期（留空表示月末到期）")
        return day



class ContractTerminatePayload(BaseModel):
    end_date: Optional[date] = None
    notes: Optional[str] = ""


class ContractRoomReducePayload(BaseModel):
    end_date: date
    notes: Optional[str] = ""


class ContractRoomSwapPayload(BaseModel):
    end_date: date
    new_room: ContractRoomPayload
    notes: Optional[str] = ""


class ContractChangeConfirmPayload(BaseModel):
    contract: ContractCreate


class ContractLifecycleItemOut(BaseModel):
    id: int
    contract_no: str
    customer_id: Optional[int] = None
    tenant_name: str = ""
    lease_type: str = ""
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: str = ""
    disposal: str = ""
    room_no: str = ""


class ContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_no: str
    payment_due_day: Optional[int] = None
    customer_id: Optional[int] = None
    customer: Optional[CustomerSummary] = None
    tower: str
    room_no: str
    tenant_name: str
    legacy_tenant_name: str = ""
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
    internal_contact_person: str
    internal_contact_feishu_id: str
    deposit: float
    deposit_status: str
    property_fee_rate: float
    monthly_property_fee: float
    prev_contract_no: str
    notes: str
    pdf_path: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    room_items: list[ContractRoomOut] = Field(default_factory=list)
    rent_terms: list[RentTermOut] = Field(default_factory=list)
    deposits: list[DepositOut] = Field(default_factory=list)
    deposit_summary: DepositSummary = Field(default_factory=DepositSummary)
    attachments: list[AttachmentOut] = Field(default_factory=list)
    attachment_summary: AttachmentSummary = Field(default_factory=AttachmentSummary)
    risks: list[dict] = Field(default_factory=list)
    previous_contract: Optional[ContractLifecycleItemOut] = None
    next_contracts: list[ContractLifecycleItemOut] = Field(default_factory=list)


class ReceivableRecordPayload(BaseModel):
    month: str
    received_amount: Optional[float] = 0
    received_date: Optional[date] = None
    invoice_status: Optional[str] = "未开票"
    invoice_no: Optional[str] = ""
    invoice_date: Optional[date] = None
    invoice_amount: Optional[float] = 0
    invoice_tax_rate: Optional[float] = 0
    invoice_notes: Optional[str] = ""
    dunning_status: Optional[str] = "未催收"
    dunning_owner: Optional[str] = ""
    last_dunning_date: Optional[date] = None
    next_follow_up_date: Optional[date] = None
    dunning_notes: Optional[str] = ""
    notes: Optional[str] = ""


class ReceivableMetadataPayload(BaseModel):
    month: str
    invoice_status: Optional[str] = "未开票"
    invoice_no: Optional[str] = ""
    invoice_date: Optional[date] = None
    invoice_amount: Optional[float] = 0
    invoice_tax_rate: Optional[float] = 0
    invoice_notes: Optional[str] = ""
    dunning_status: Optional[str] = "未催收"
    dunning_owner: Optional[str] = ""
    last_dunning_date: Optional[date] = None
    next_follow_up_date: Optional[date] = None
    dunning_notes: Optional[str] = ""
    notes: Optional[str] = ""


class ReceivablePaymentPayload(BaseModel):
    operation_id: Optional[str] = Field(default=None, min_length=36, max_length=36, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    metadata: Optional[ReceivableMetadataPayload] = None
    month: str
    amount: float = Field(allow_inf_nan=False)
    payment_date: Optional[date] = None
    allocation_type: Optional[str] = "收款"
    payment_method: Optional[str] = ""
    reference_no: Optional[str] = ""
    notes: Optional[str] = ""


class ReceivablePaymentReceipt(BaseModel):
    operation_id: str
    contract_id: int
    payment_id: Optional[int]
    status: Literal["recorded", "deleted"]
    replayed: bool = False


class LeaseDocumentPayload(BaseModel):
    document_type: str
    contract_id: Optional[int] = None
    month: Optional[str] = None
    payment_id: Optional[int] = None
    notice_date: Optional[date] = None
    payment_instruction: Optional[str] = ""
    notes: Optional[str] = ""


class ContractListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ContractOut]


class LogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: Optional[int] = None
    target_type: Optional[str] = ""
    target_id: Optional[int] = None
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
