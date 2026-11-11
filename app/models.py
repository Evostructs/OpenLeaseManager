from __future__ import annotations

import calendar
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from app.core.money import round_money
from app.core.time import now_in_timezone, today_in_timezone
from app.database import Base

# 金额列统一 NUMERIC(18,2)：SQLite 下行为与 REAL 等价（asdecimal=False 仍返回数值），
# 但 DDL 明确金额语义，迁移到 PostgreSQL/MySQL 时自动获得精确 DECIMAL 存储
MONEY = Numeric(18, 2, asdecimal=False)
AREA = Numeric(18, 4, asdecimal=False)
METRIC_VALUE = Numeric(24, 6, asdecimal=False)


class Portfolio(Base):
    __tablename__ = "portfolios"
    __table_args__ = (
        CheckConstraint("length(trim(code)) > 0", name="ck_portfolios_code_nonempty"),
        Index("ux_portfolios_code_normalized", text("lower(trim(code))"), unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(150), nullable=False)
    is_provisional = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)
    updated_at = Column(DateTime, nullable=False, default=now_in_timezone, onupdate=now_in_timezone)


class Building(Base):
    __tablename__ = "buildings"
    __table_args__ = (
        CheckConstraint("length(trim(code)) > 0", name="ck_buildings_code_nonempty"),
        CheckConstraint(
            "ingestion_mode IN ('detail', 'snapshot', 'external')",
            name="ck_buildings_ingestion_mode",
        ),
        Index("ux_buildings_code_normalized", text("lower(trim(code))"), unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(150), nullable=False)
    timezone = Column(String(64), nullable=False, default="Asia/Shanghai")
    currency = Column(String(3), nullable=False, default="CNY")
    ingestion_mode = Column(String(20), nullable=False, default="detail")
    is_provisional = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)
    updated_at = Column(DateTime, nullable=False, default=now_in_timezone, onupdate=now_in_timezone)


class PortfolioBuilding(Base):
    __tablename__ = "portfolio_buildings"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "building_id", "valid_from", name="uq_portfolio_building_from"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_portfolio_buildings_valid_range"),
    )

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False, index=True)
    building_id = Column(Integer, ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=False, index=True)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)


class Zone(Base):
    __tablename__ = "zones"
    __table_args__ = (
        UniqueConstraint("building_id", "code", name="uq_zones_building_code"),
        UniqueConstraint("building_id", "id", name="uq_zones_building_id_id"),
        CheckConstraint("length(trim(code)) > 0", name="ck_zones_code_nonempty"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_zones_valid_range"),
    )

    id = Column(Integer, primary_key=True, index=True)
    building_id = Column(Integer, ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=False, index=True)
    code = Column(String(50), nullable=False)
    name = Column(String(100), nullable=False)
    zone_type = Column(String(30), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)
    updated_at = Column(DateTime, nullable=False, default=now_in_timezone, onupdate=now_in_timezone)


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (
        UniqueConstraint("building_id", "id", name="uq_contracts_building_id_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    contract_no = Column(String(50), unique=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=False, index=True)
    tower = Column(String(10))
    room_no = Column(String(50), index=True)
    tenant_name = Column(String(100), index=True)
    area = Column(Float, default=0)
    unit_price = Column(MONEY, default=0)
    lease_type = Column(String(20))
    free_period = Column(Integer, default=0)
    monthly_rent = Column(MONEY, default=0)
    broker_fee = Column(MONEY, default=0)
    start_date = Column(Date, index=True)
    end_date = Column(Date, index=True)
    disposal = Column(String(20), default="")
    contact_person = Column(String(50), default="")
    contact_phone = Column(String(30), default="")
    contact_email = Column(String(100), default="")
    internal_contact_person = Column(String(50), default="")
    internal_contact_feishu_id = Column(String(100), default="")
    deposit = Column(MONEY, default=0)
    deposit_status = Column(String(20), default="")
    property_fee_rate = Column(MONEY, default=0)
    property_fee_free = Column(Integer, default=0)
    prev_contract_no = Column(String(50), default="")
    # 付款日：每月 X 日租金到期；空 = 月末到期（旧合同默认口径）
    payment_due_day = Column(Integer, nullable=True)
    notes = Column(Text, default="")
    pdf_path = Column(String(500), default="")
    is_deleted = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=now_in_timezone)
    updated_at = Column(DateTime, default=now_in_timezone, onupdate=now_in_timezone)

    customer = relationship("Customer", back_populates="contracts")
    building = relationship("Building", foreign_keys=[building_id], viewonly=True)
    logs = relationship("OperationLog", back_populates="contract")
    customer_follow_ups = relationship(
        "CustomerFollowUp",
        back_populates="contract",
        foreign_keys="CustomerFollowUp.contract_id",
    )
    room_items = relationship(
        "ContractRoom",
        back_populates="contract",
        cascade="all, delete-orphan",
        order_by="ContractRoom.id",
        foreign_keys="ContractRoom.contract_id",
    )
    receivable_records = relationship("ReceivableRecord", back_populates="contract", cascade="all, delete-orphan")
    receivable_payments = relationship("ReceivablePayment", back_populates="contract", cascade="all, delete-orphan")
    deposits = relationship("ContractDeposit", back_populates="contract", cascade="all, delete-orphan", order_by="ContractDeposit.id")
    rent_terms = relationship(
        "RentTerm",
        back_populates="contract",
        cascade="all, delete-orphan",
        order_by="RentTerm.id",
    )

    @property
    def monthly_property_fee(self) -> float:
        return round_money((self.area or 0) * (self.property_fee_rate or 0))

    @property
    def net_property_fee(self) -> float:
        return self.monthly_property_fee

    @property
    def net_rent(self) -> float:
        # 单一口径：委托租金引擎（运行时引入避免模块级循环依赖）
        from app.services.contracts import contract_net_rent

        return contract_net_rent(self)

    @property
    def total_months(self) -> int:
        if not self.start_date or not self.end_date:
            return 0
        if self.end_date < self.start_date:
            return 0
        months = (self.end_date.year - self.start_date.year) * 12 + (self.end_date.month - self.start_date.month) + 1
        return max(0, months)

    @property
    def status(self) -> str:
        today = today_in_timezone()
        if not self.start_date or not self.end_date:
            return "信息不全"
        if today < self.start_date:
            return "待生效"
        if self.end_date < today:
            if self.disposal in {"已续租", "已退租", "逾期占用", "已变更"}:
                return self.disposal
            return "已到期"
        if (self.end_date - today).days <= 90:
            return "即将到期"
        return "在租"

    @property
    def days_to_expiry(self) -> int | None:
        if not self.end_date:
            return None
        return (self.end_date - today_in_timezone()).days


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    short_name = Column(String(100), default="")
    credit_code = Column(String(50), default="", index=True)
    customer_type = Column(String(50), default="")
    industry = Column(String(100), default="")
    contact_name = Column(String(50), default="")
    contact_phone = Column(String(30), default="")
    contact_email = Column(String(100), default="")
    address = Column(Text, default="")
    notes = Column(Text, default="")
    status = Column(String(20), default="active", index=True)
    created_at = Column(DateTime, default=now_in_timezone)
    updated_at = Column(DateTime, default=now_in_timezone, onupdate=now_in_timezone)

    contracts = relationship("Contract", back_populates="customer")
    follow_ups = relationship("CustomerFollowUp", back_populates="customer", cascade="all, delete-orphan")
    deposits = relationship("ContractDeposit", back_populates="customer")


class CustomerFollowUp(Base):
    __tablename__ = "customer_follow_ups"
    __table_args__ = (
        ForeignKeyConstraint(
            ["building_id", "contract_id"],
            ["contracts.building_id", "contracts.id"],
            name="fk_customer_follow_ups_building_contract",
            ondelete="RESTRICT",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=True, index=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=False, index=True)
    follow_up_type = Column(String(20), default="电话", index=True)
    occurred_at = Column(DateTime, nullable=False, default=now_in_timezone, index=True)
    content = Column(Text, default="")
    next_action = Column(Text, default="")
    next_follow_up_date = Column(Date, nullable=True, index=True)
    owner = Column(String(50), default="", index=True)
    status = Column(String(20), default="open", index=True)
    created_at = Column(DateTime, default=now_in_timezone)
    updated_at = Column(DateTime, default=now_in_timezone, onupdate=now_in_timezone)

    customer = relationship("Customer", back_populates="follow_ups")
    contract = relationship(
        "Contract",
        back_populates="customer_follow_ups",
        foreign_keys=[contract_id],
    )


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (
        UniqueConstraint("building_id", "room_no", name="uq_rooms_building_room_no"),
        UniqueConstraint(
            "building_id",
            "id",
            "room_no",
            name="uq_rooms_building_id_id_room_no",
        ),
        ForeignKeyConstraint(
            ["building_id", "zone_id"],
            ["zones.building_id", "zones.id"],
            name="fk_rooms_building_zone",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "room_no = trim(room_no) AND length(room_no) > 0",
            name="ck_rooms_room_no_canonical",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=False, index=True)
    zone_id = Column(Integer, ForeignKey("zones.id"), nullable=False, index=True)
    tower = Column(String(10))
    floor = Column(String(10))
    room_no = Column(String(50), nullable=False, index=True)
    room_type = Column(String(20), default="办公")
    area = Column(Float, default=0)
    is_leasable = Column(Boolean, default=True)
    notes = Column(Text, default="")
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=now_in_timezone)
    updated_at = Column(DateTime, default=now_in_timezone, onupdate=now_in_timezone)

    building = relationship("Building", foreign_keys=[building_id], viewonly=True)


class ContractRoom(Base):
    __tablename__ = "contract_rooms"
    __table_args__ = (
        ForeignKeyConstraint(
            ["building_id", "contract_id"],
            ["contracts.building_id", "contracts.id"],
            name="fk_contract_rooms_building_contract",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["building_id", "room_id", "room_no"],
            ["rooms.building_id", "rooms.id", "rooms.room_no"],
            name="fk_contract_rooms_building_room_number",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        CheckConstraint(
            "room_no = trim(room_no) AND length(room_no) > 0",
            name="ck_contract_rooms_room_no_canonical",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), index=True, nullable=False)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=False, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)
    tower = Column(String(10), default="")
    room_no = Column(String(50), nullable=False, index=True)
    area = Column(Float, default=0)
    unit_price = Column(MONEY, default=0)
    monthly_rent = Column(MONEY, default=0)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=now_in_timezone)
    updated_at = Column(DateTime, default=now_in_timezone, onupdate=now_in_timezone)

    contract = relationship(
        "Contract",
        back_populates="room_items",
        foreign_keys=[contract_id],
    )
    room = relationship("Room", foreign_keys=[room_id])


class RentTerm(Base):
    __tablename__ = "rent_terms"

    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), index=True, nullable=False)
    contract_room_id = Column(Integer, ForeignKey("contract_rooms.id"), nullable=True, index=True)
    room_no = Column(String(50), nullable=True, index=True)
    rule_type = Column(String(30), index=True, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    area = Column(Float, nullable=True)
    unit_price = Column(MONEY, nullable=True)
    monthly_amount = Column(MONEY, nullable=True)
    adjustment_type = Column(String(20), nullable=True)
    adjustment_value = Column(MONEY, nullable=True)
    frequency = Column(String(20), nullable=True)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=now_in_timezone)
    updated_at = Column(DateTime, default=now_in_timezone, onupdate=now_in_timezone)

    contract = relationship("Contract", back_populates="rent_terms")
    contract_room = relationship("ContractRoom")


class ReceivableStatement(Base):
    """月度出账固化：出账后该月应收金额以本表为准，合同条款变更不再回溯影响。"""

    __tablename__ = "receivable_statements"
    __table_args__ = (UniqueConstraint("contract_id", "period_month", name="uq_statement_contract_month"),)

    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), index=True, nullable=False)
    period_month = Column(String(7), index=True, nullable=False)
    rent = Column(MONEY, nullable=False, default=0)
    property_fee = Column(MONEY, nullable=False, default=0)
    total_amount = Column(MONEY, nullable=False, default=0)
    source = Column(String(20), nullable=False, default="auto")
    operator = Column(String(50), nullable=False, default="")
    generated_at = Column(DateTime, default=now_in_timezone)


class ReceivableRecord(Base):
    __tablename__ = "receivable_records"
    __table_args__ = (UniqueConstraint("contract_id", "period_month", name="uq_receivable_contract_month"),)

    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), index=True, nullable=False)
    period_month = Column(String(7), index=True, nullable=False)
    received_amount = Column(MONEY, default=0)
    received_date = Column(Date, nullable=True)
    invoice_status = Column(String(20), default="未开票")
    invoice_no = Column(String(100), default="")
    invoice_date = Column(Date, nullable=True)
    invoice_amount = Column(MONEY, default=0)
    invoice_tax_rate = Column(Float, default=0)
    invoice_notes = Column(Text, default="")
    dunning_status = Column(String(20), default="未催收")
    dunning_owner = Column(String(50), default="")
    last_dunning_date = Column(Date, nullable=True)
    next_follow_up_date = Column(Date, nullable=True)
    dunning_notes = Column(Text, default="")
    notes = Column(Text, default="")
    operator = Column(String(50), default="")
    created_at = Column(DateTime, default=now_in_timezone)
    updated_at = Column(DateTime, default=now_in_timezone, onupdate=now_in_timezone)

    contract = relationship("Contract", back_populates="receivable_records")


class ReceivablePayment(Base):
    __tablename__ = "receivable_payments"

    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), index=True, nullable=False)
    period_month = Column(String(7), index=True, nullable=False)
    payment_date = Column(Date, nullable=True)
    amount = Column(MONEY, default=0)
    allocation_type = Column(String(20), default="收款")
    payment_method = Column(String(20), default="")
    reference_no = Column(String(100), default="")
    notes = Column(Text, default="")
    operator = Column(String(50), default="")
    source = Column(String(20), default="manual")
    created_at = Column(DateTime, default=now_in_timezone)
    updated_at = Column(DateTime, default=now_in_timezone, onupdate=now_in_timezone)

    contract = relationship("Contract", back_populates="receivable_payments")


class ReceivablePaymentOperation(Base):
    """Keep a receipt after payment deletion so an old retry cannot recreate it."""
    __tablename__ = "receivable_payment_operations"

    operation_id = Column(String(36), primary_key=True)
    contract_id = Column(Integer, ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_id = Column(Integer, ForeignKey("receivable_payments.id", ondelete="SET NULL"), nullable=True, index=True)
    request_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=now_in_timezone, nullable=False)


class ContractDeposit(Base):
    __tablename__ = "contract_deposits"

    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), index=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    deposit_type = Column(String(30), default="租赁押金", index=True)
    expected_amount = Column(MONEY, default=0)
    received_amount = Column(MONEY, default=0)
    refunded_amount = Column(MONEY, default=0)
    offset_amount = Column(MONEY, default=0)
    status = Column(String(20), default="unpaid", index=True)
    due_date = Column(Date, nullable=True)
    received_date = Column(Date, nullable=True)
    refund_date = Column(Date, nullable=True)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=now_in_timezone)
    updated_at = Column(DateTime, default=now_in_timezone, onupdate=now_in_timezone)

    contract = relationship("Contract", back_populates="deposits")
    customer = relationship("Customer", back_populates="deposits")


class Attachment(Base):
    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True, index=True)
    scope_type = Column(String(20), nullable=True, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=True, index=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=True, index=True)
    entity_type = Column(String(30), nullable=False, index=True)
    entity_id = Column(Integer, nullable=False, index=True)
    file_name = Column(String(255), nullable=False)
    storage_path = Column(String(500), nullable=False, unique=True)
    content_type = Column(String(120), default="")
    file_size = Column(Integer, default=0)
    category = Column(String(30), default="其他", index=True)
    notes = Column(Text, default="")
    uploaded_by = Column(String(50), default="")
    created_at = Column(DateTime, default=now_in_timezone)
    deleted_at = Column(DateTime, nullable=True, index=True)


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=True)
    scope_type = Column(String(20), nullable=True, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=True, index=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=True, index=True)
    target_type = Column(String(30), default="", index=True)
    target_id = Column(Integer, nullable=True, index=True)
    action = Column(String(20))
    changes = Column(Text, default="")
    operator = Column(String(50), default="system")
    timestamp = Column(DateTime, default=now_in_timezone, index=True)

    contract = relationship("Contract", back_populates="logs")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True)
    display_name = Column(String(50), default="")
    password_hash = Column(String(200))
    role = Column(String(20), default="editor")
    is_active = Column(Boolean, default=True)
    session_version = Column(Integer, default=0)
    authorization_revision = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, default=now_in_timezone)


class BuildingOperatingGateEvent(Base):
    """Append-only decision history for portfolio presentation and aggregation."""

    __tablename__ = "building_operating_gate_events"
    __table_args__ = (
        UniqueConstraint("building_id", "revision", name="uq_building_operating_gate_revision"),
        UniqueConstraint("building_id", "id", name="uq_building_operating_gate_building_id"),
        ForeignKeyConstraint(
            ["building_id", "previous_event_id"],
            ["building_operating_gate_events.building_id", "building_operating_gate_events.id"],
            name="fk_building_operating_gate_previous_same_building",
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision > 0", name="ck_building_operating_gate_revision_positive"),
        CheckConstraint(
            "presentation_enabled IN (0, 1)",
            name="ck_building_operating_gate_presentation_boolean",
        ),
        CheckConstraint(
            "contribution_enabled IN (0, 1)",
            name="ck_building_operating_gate_contribution_boolean",
        ),
        CheckConstraint("length(trim(reason)) > 0", name="ck_building_operating_gate_reason_nonempty"),
        CheckConstraint(
            "origin IN ('migration', 'user')",
            name="ck_building_operating_gate_origin",
        ),
        CheckConstraint(
            "(origin = 'migration' AND actor_id IS NULL) OR "
            "(origin = 'user' AND actor_id IS NOT NULL)",
            name="ck_building_operating_gate_actor_origin",
        ),
        CheckConstraint(
            "(revision = 1 AND previous_event_id IS NULL) OR "
            "(revision > 1 AND previous_event_id IS NOT NULL)",
            name="ck_building_operating_gate_previous_revision",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    building_id = Column(Integer, ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    presentation_enabled = Column(Boolean, nullable=False)
    contribution_enabled = Column(Boolean, nullable=False)
    reason = Column(Text, nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True)
    origin = Column(String(20), nullable=False)
    previous_event_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone, index=True)


class AuthorizationControl(Base):
    __tablename__ = "authorization_control"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_authorization_control_singleton"),
        CheckConstraint("global_revision >= 0", name="ck_authorization_control_revision"),
    )

    id = Column(Integer, primary_key=True)
    global_revision = Column(Integer, nullable=False, default=0, server_default="0")
    updated_at = Column(DateTime, nullable=False, default=now_in_timezone, onupdate=now_in_timezone)


class UserAuthorizationRevision(Base):
    """Append-only authorization document history; database triggers reject mutation on SQLite."""

    __tablename__ = "user_authorization_revisions"
    __table_args__ = (
        UniqueConstraint("target_user_id", "revision", name="uq_user_authorization_revision"),
        UniqueConstraint("global_revision", name="uq_user_authorization_global_revision"),
        CheckConstraint("revision > 0", name="ck_user_authorization_revision_positive"),
        CheckConstraint("global_revision > 0", name="ck_user_authorization_global_revision_positive"),
    )

    id = Column(Integer, primary_key=True, index=True)
    target_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    global_revision = Column(Integer, nullable=False)
    operator_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    operator_username = Column(String(50), nullable=False)
    before_state = Column(Text, nullable=False)
    after_state = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone, index=True)


class CustomerBuildingProfile(Base):
    __tablename__ = "customer_building_profiles"
    __table_args__ = (
        UniqueConstraint("customer_id", "building_id", "valid_from", name="uq_customer_building_profile_from"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_customer_building_profiles_valid_range"),
    )

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True)
    building_id = Column(Integer, ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=False, index=True)
    local_contact = Column(Text, nullable=False, default="")
    local_notes = Column(Text, nullable=False, default="")
    owner = Column(String(100), nullable=False, default="")
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)
    updated_at = Column(DateTime, nullable=False, default=now_in_timezone, onupdate=now_in_timezone)


class UserScopeGrant(Base):
    __tablename__ = "user_scope_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "portfolio_id", "valid_from", name="uq_scope_user_portfolio_from"),
        UniqueConstraint("user_id", "building_id", "valid_from", name="uq_scope_user_building_from"),
        CheckConstraint(
            "(scope_type = 'portfolio' AND portfolio_id IS NOT NULL AND building_id IS NULL) "
            "OR (scope_type = 'building' AND building_id IS NOT NULL AND portfolio_id IS NULL)",
            name="ck_user_scope_grants_target",
        ),
        CheckConstraint("access_level IN ('view', 'edit')", name="ck_user_scope_grants_access_level"),
        CheckConstraint(
            "scope_type = 'portfolio' OR include_future_buildings = 0",
            name="ck_user_scope_grants_future_scope",
        ),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_user_scope_grants_valid_range"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    scope_type = Column(String(20), nullable=False, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=True, index=True)
    building_id = Column(Integer, ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=True, index=True)
    access_level = Column(String(10), nullable=False)
    include_future_buildings = Column(Boolean, nullable=False, default=False)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)
    granted_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    granted_at = Column(DateTime, nullable=False, default=now_in_timezone)


class UserPortfolioOverviewGrant(Base):
    """A narrow portfolio-summary grant that never expands into building data access."""

    __tablename__ = "user_portfolio_overview_grants"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "portfolio_id",
            "valid_from",
            name="uq_user_portfolio_overview_from",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_user_portfolio_overview_valid_range",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    portfolio_id = Column(
        Integer,
        ForeignKey("portfolios.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    include_future_buildings = Column(Boolean, nullable=False, default=False)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)
    granted_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    granted_at = Column(DateTime, nullable=False, default=now_in_timezone)


class UserCapability(Base):
    __tablename__ = "user_capabilities"
    __table_args__ = (
        UniqueConstraint("user_id", "capability", "valid_from", name="uq_user_capability_from"),
        CheckConstraint(
            "capability IN ("
            "'platform_admin', 'customer_master_manage', 'data_import_submit', "
            "'snapshot_review', 'snapshot_publish', 'metric_definition_manage', "
            "'snapshot_exchange_receive'"
            ")",
            name="ck_user_capabilities_name",
        ),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_user_capabilities_valid_range"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    capability = Column(String(50), nullable=False, index=True)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)
    granted_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    granted_at = Column(DateTime, nullable=False, default=now_in_timezone)


class DataSource(Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("building_id", "name", name="uq_data_sources_building_name"),
        CheckConstraint(
            "source_type IN ('detail', 'manual_snapshot', 'excel', 'external_api')",
            name="ck_data_sources_type",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    building_id = Column(Integer, ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_type = Column(String(30), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    owner = Column(String(100), nullable=False, default="")
    cadence = Column(String(50), nullable=False)
    freshness_rule = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)
    updated_at = Column(DateTime, nullable=False, default=now_in_timezone, onupdate=now_in_timezone)


class SnapshotExchangeSourceBinding(Base):
    __tablename__ = "snapshot_exchange_source_bindings"
    __table_args__ = (
        UniqueConstraint("source_id", name="uq_snapshot_exchange_bindings_source"),
        CheckConstraint(
            "length(trim(source_system_code)) > 0",
            name="ck_snapshot_exchange_bindings_code_nonempty",
        ),
        CheckConstraint(
            "is_active IN (0, 1)",
            name="ck_snapshot_exchange_bindings_active_boolean",
        ),
        Index(
            "ux_snapshot_exchange_bindings_code_normalized",
            text("lower(trim(source_system_code))"),
            unique=True,
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(
        Integer,
        ForeignKey("data_sources.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_system_code = Column(String(100), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)
    updated_at = Column(DateTime, nullable=False, default=now_in_timezone, onupdate=now_in_timezone)


class SnapshotExchangeReceipt(Base):
    __tablename__ = "snapshot_exchange_receipts"
    __table_args__ = (
        UniqueConstraint(
            "binding_id",
            "delivery_id",
            "content_digest",
            "status",
            name="uq_snapshot_exchange_receipts_attempt",
        ),
        CheckConstraint(
            "status IN ('accepted', 'duplicate', 'rejected', 'conflict')",
            name="ck_snapshot_exchange_receipts_status",
        ),
        CheckConstraint(
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
        CheckConstraint(
            "length(content_digest) = 64 "
            "AND content_digest = lower(content_digest) "
            "AND content_digest NOT GLOB '*[^0-9a-f]*'",
            name="ck_snapshot_exchange_receipts_digest_shape",
        ),
        CheckConstraint(
            "period_end >= period_start AND as_of_date >= period_start AND as_of_date <= period_end",
            name="ck_snapshot_exchange_receipts_period",
        ),
        CheckConstraint(
            "source_publication_revision > 0",
            name="ck_snapshot_exchange_receipts_source_revision",
        ),
        CheckConstraint(
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
        CheckConstraint(
            "correction_of_receipt_id IS NULL OR correction_of_receipt_id <> id",
            name="ck_snapshot_exchange_receipts_correction_not_self",
        ),
        CheckConstraint(
            "original_receipt_id IS NULL OR original_receipt_id <> id",
            name="ck_snapshot_exchange_receipts_original_not_self",
        ),
        Index(
            "ux_snapshot_exchange_receipts_canonical_delivery",
            "binding_id",
            "delivery_id",
            unique=True,
            sqlite_where=text("status IN ('accepted', 'duplicate', 'rejected')"),
            postgresql_where=text("status IN ('accepted', 'duplicate', 'rejected')"),
        ),
        Index(
            "ux_snapshot_exchange_receipts_accepted_content",
            "binding_id",
            "content_digest",
            unique=True,
            sqlite_where=text("status = 'accepted'"),
            postgresql_where=text("status = 'accepted'"),
        ),
        Index(
            "ux_snapshot_exchange_receipts_accepted_revision",
            "binding_id",
            "period_start",
            "period_end",
            "as_of_date",
            "source_publication_revision",
            unique=True,
            sqlite_where=text("status = 'accepted'"),
            postgresql_where=text("status = 'accepted'"),
        ),
        Index(
            "ux_snapshot_exchange_receipts_accepted_root",
            "binding_id",
            "period_start",
            "period_end",
            "as_of_date",
            unique=True,
            sqlite_where=text(
                "status = 'accepted' AND correction_of_receipt_id IS NULL"
            ),
            postgresql_where=text(
                "status = 'accepted' AND correction_of_receipt_id IS NULL"
            ),
        ),
        Index(
            "ux_snapshot_exchange_receipts_accepted_correction",
            "correction_of_receipt_id",
            unique=True,
            sqlite_where=text(
                "status = 'accepted' AND correction_of_receipt_id IS NOT NULL"
            ),
            postgresql_where=text(
                "status = 'accepted' AND correction_of_receipt_id IS NOT NULL"
            ),
        ),
        Index(
            "ux_snapshot_exchange_receipts_accepted_snapshot",
            "candidate_snapshot_id",
            unique=True,
            sqlite_where=text("status = 'accepted'"),
            postgresql_where=text("status = 'accepted'"),
        ),
        Index(
            "ux_snapshot_exchange_receipts_accepted_batch",
            "candidate_batch_id",
            unique=True,
            sqlite_where=text("status = 'accepted'"),
            postgresql_where=text("status = 'accepted'"),
        ),
        Index(
            "ux_snapshot_exchange_receipts_accepted_candidate_ref",
            "candidate_ref",
            unique=True,
            sqlite_where=text("status = 'accepted'"),
            postgresql_where=text("status = 'accepted'"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    receipt_no = Column(String(150), unique=True, nullable=False, index=True)
    binding_id = Column(
        Integer,
        ForeignKey("snapshot_exchange_source_bindings.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    delivery_id = Column(String(100), nullable=False, index=True)
    exchange_version = Column(String(50), nullable=False)
    metric_definition_version = Column(String(50), nullable=False)
    digest_profile = Column(String(50), nullable=False)
    digest_algorithm = Column(String(20), nullable=False)
    content_digest = Column(String(128), nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True)
    period_start = Column(Date, nullable=False, index=True)
    period_end = Column(Date, nullable=False, index=True)
    as_of_date = Column(Date, nullable=False, index=True)
    source_publication_revision = Column(Integer, nullable=False)
    source_publication_ref = Column(String(150), nullable=False)
    correction_of_receipt_id = Column(
        Integer,
        ForeignKey("snapshot_exchange_receipts.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    original_receipt_id = Column(
        Integer,
        ForeignKey("snapshot_exchange_receipts.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    candidate_snapshot_id = Column(
        Integer,
        ForeignKey("building_operating_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    candidate_batch_id = Column(
        Integer,
        ForeignKey("import_batches.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    candidate_ref = Column(String(150), nullable=True, index=True)
    normalized_business_json = Column(Text, nullable=False)
    errors_json = Column(Text, nullable=False, default="[]")
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_username = Column(String(100), nullable=False)
    received_at = Column(DateTime, nullable=False, default=now_in_timezone, index=True)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        UniqueConstraint("source_id", "external_batch_no", name="uq_import_batches_external_no"),
        UniqueConstraint("source_id", "file_checksum", name="uq_import_batches_file_checksum"),
        CheckConstraint(
            "status IN ('received', 'validating', 'rejected', 'reviewed', 'published', 'superseded')",
            name="ck_import_batches_status",
        ),
        CheckConstraint(
            "row_count >= 0 AND accepted_count >= 0 AND rejected_count >= 0 "
            "AND accepted_count + rejected_count <= row_count",
            name="ck_import_batches_counts",
        ),
        CheckConstraint(
            "(reviewed_by IS NULL AND reviewed_at IS NULL) OR "
            "(reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_import_batches_review_pair",
        ),
        CheckConstraint(
            "supersedes_id IS NULL OR "
            "(correction_reason IS NOT NULL AND length(trim(correction_reason)) > 0)",
            name="ck_import_batches_correction_reason",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("data_sources.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_file_name = Column(String(255), nullable=True)
    external_batch_no = Column(String(100), nullable=True)
    period = Column(String(20), nullable=True, index=True)
    as_of_date = Column(Date, nullable=False, index=True)
    source_observed_at = Column(DateTime, nullable=True)
    source_updated_at = Column(DateTime, nullable=True)
    received_at = Column(DateTime, nullable=False, default=now_in_timezone)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    ingested_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)
    file_checksum = Column(String(128), nullable=True)
    parser_version = Column(String(100), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    row_count = Column(Integer, nullable=False, default=0)
    accepted_count = Column(Integer, nullable=False, default=0)
    rejected_count = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="received", index=True)
    error_summary = Column(Text, nullable=False, default="")
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    supersedes_id = Column(Integer, ForeignKey("import_batches.id", ondelete="RESTRICT"), nullable=True, index=True)
    correction_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)


class SourceRecord(Base):
    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint("batch_id", "external_record_id", name="uq_source_records_external_id"),
        UniqueConstraint("batch_id", "row_no", name="uq_source_records_row_no"),
        CheckConstraint("external_record_id IS NOT NULL OR row_no IS NOT NULL", name="ck_source_records_locator"),
        CheckConstraint("row_no IS NULL OR row_no > 0", name="ck_source_records_positive_row"),
        CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name="ck_source_records_status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("import_batches.id", ondelete="RESTRICT"), nullable=False, index=True)
    external_record_id = Column(String(150), nullable=True)
    row_no = Column(Integer, nullable=True)
    record_checksum = Column(String(128), nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)
    business_object_type = Column(String(50), nullable=True, index=True)
    business_object_id = Column(Integer, nullable=True, index=True)
    source_ref = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)


class ImportError(Base):
    __tablename__ = "import_errors"
    __table_args__ = (
        CheckConstraint("row_no IS NULL OR row_no > 0", name="ck_import_errors_positive_row"),
        CheckConstraint("severity IN ('warning', 'error', 'blocking')", name="ck_import_errors_severity"),
        CheckConstraint("length(trim(error_code)) > 0", name="ck_import_errors_code_nonempty"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("import_batches.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_record_id = Column(Integer, ForeignKey("source_records.id", ondelete="RESTRICT"), nullable=True, index=True)
    row_no = Column(Integer, nullable=True)
    field_name = Column(String(100), nullable=True)
    error_code = Column(String(100), nullable=False, index=True)
    severity = Column(String(20), nullable=False, default="error", index=True)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)


class AreaMeasurement(Base):
    __tablename__ = "area_measurements"
    __table_args__ = (
        UniqueConstraint(
            "source_record_id",
            "area_type",
            "measurement_purpose",
            "version",
            name="uq_area_measurements_source_kind_version",
        ),
        CheckConstraint("zone_id IS NULL OR room_id IS NULL", name="ck_area_measurements_single_target"),
        CheckConstraint("area_sqm >= 0", name="ck_area_measurements_nonnegative"),
        CheckConstraint("version > 0", name="ck_area_measurements_version"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_area_measurements_valid_range"),
        CheckConstraint(
            "quality_status IN ('draft', 'reviewed', 'approved', 'rejected')",
            name="ck_area_measurements_quality_status",
        ),
        CheckConstraint(
            "(approved_by IS NULL AND approved_at IS NULL) OR "
            "(approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_area_measurements_approval_pair",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    building_id = Column(Integer, ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=False, index=True)
    zone_id = Column(Integer, ForeignKey("zones.id", ondelete="RESTRICT"), nullable=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=True, index=True)
    area_type = Column(String(50), nullable=False, index=True)
    measurement_purpose = Column(String(100), nullable=False)
    area_sqm = Column(AREA, nullable=False)
    standard = Column(String(100), nullable=False)
    boundary_method = Column(Text, nullable=False)
    measured_at = Column(Date, nullable=False)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    source_record_id = Column(Integer, ForeignKey("source_records.id", ondelete="RESTRICT"), nullable=False, index=True)
    evidence_ref = Column(Text, nullable=False, default="")
    approved_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    quality_status = Column(String(20), nullable=False, default="draft", index=True)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)


class MetricDefinition(Base):
    __tablename__ = "metric_definitions"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_metric_definitions_code_version"),
        CheckConstraint("length(trim(code)) > 0", name="ck_metric_definitions_code_nonempty"),
        CheckConstraint("version > 0", name="ck_metric_definitions_version"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_metric_definitions_valid_range"),
        CheckConstraint(
            "publication_status IN ('draft', 'published')",
            name="ck_metric_definitions_publication_status",
        ),
        CheckConstraint(
            "(publication_status = 'draft' AND published_by IS NULL AND published_at IS NULL) OR "
            "(publication_status = 'published' AND published_by IS NOT NULL AND published_at IS NOT NULL)",
            name="ck_metric_definitions_publication_pair",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(100), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    business_definition = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)
    grain = Column(String(30), nullable=False)
    unit = Column(String(50), nullable=False)
    currency = Column(String(3), nullable=True)
    numerator_rule = Column(Text, nullable=False)
    denominator_rule = Column(Text, nullable=False)
    inclusion_rule = Column(Text, nullable=False)
    exclusion_rule = Column(Text, nullable=False)
    aggregation_rule = Column(Text, nullable=False)
    coverage_rule = Column(Text, nullable=False)
    estimation_policy = Column(Text, nullable=False)
    freshness_policy = Column(Text, nullable=False)
    owner = Column(String(100), nullable=False)
    definition_hash = Column(String(128), nullable=False)
    publication_status = Column(String(20), nullable=False, default="draft", index=True)
    published_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)


class BuildingOperatingSnapshot(Base):
    __tablename__ = "building_operating_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "building_id",
            "period_start",
            "period_end",
            "as_of_date",
            "revision",
            name="uq_building_snapshots_revision",
        ),
        CheckConstraint("period_end >= period_start", name="ck_building_snapshots_period_range"),
        CheckConstraint("revision > 0", name="ck_building_snapshots_revision"),
        CheckConstraint(
            "validation_status IN ('pending', 'valid', 'rejected')",
            name="ck_building_snapshots_validation_status",
        ),
        CheckConstraint(
            "publication_status IN ('draft', 'reviewed', 'published', 'superseded')",
            name="ck_building_snapshots_publication_status",
        ),
        CheckConstraint(
            "(submitted_by IS NULL AND submitted_at IS NULL) OR "
            "(submitted_by IS NOT NULL AND submitted_at IS NOT NULL)",
            name="ck_building_snapshots_submission_pair",
        ),
        CheckConstraint(
            "(reviewed_by IS NULL AND reviewed_at IS NULL) OR "
            "(reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_building_snapshots_review_pair",
        ),
        CheckConstraint(
            "(published_by IS NULL AND published_at IS NULL) OR "
            "(published_by IS NOT NULL AND published_at IS NOT NULL)",
            name="ck_building_snapshots_publication_pair",
        ),
        CheckConstraint(
            "publication_status NOT IN ('published', 'superseded') OR published_by IS NOT NULL",
            name="ck_building_snapshots_published_actor",
        ),
        CheckConstraint(
            "is_current = 0 OR publication_status = 'published'",
            name="ck_building_snapshots_current_published",
        ),
        Index(
            "ux_building_snapshots_current_published",
            "building_id",
            "period_start",
            "period_end",
            "as_of_date",
            unique=True,
            sqlite_where=text("publication_status = 'published' AND is_current = 1"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    building_id = Column(Integer, ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=False, index=True)
    period_start = Column(Date, nullable=False, index=True)
    period_end = Column(Date, nullable=False, index=True)
    as_of_date = Column(Date, nullable=False, index=True)
    revision = Column(Integer, nullable=False, default=1)
    validation_status = Column(String(20), nullable=False, default="pending", index=True)
    publication_status = Column(String(20), nullable=False, default="draft", index=True)
    is_current = Column(Boolean, nullable=False, default=False, index=True)
    submitted_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    published_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    published_at = Column(DateTime, nullable=True)
    supersedes_id = Column(
        Integer,
        ForeignKey("building_operating_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)
    updated_at = Column(DateTime, nullable=False, default=now_in_timezone, onupdate=now_in_timezone)


class BuildingMetricFact(Base):
    __tablename__ = "building_metric_facts"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "metric_definition_id", name="uq_building_metric_fact_definition"),
        CheckConstraint("numerator IS NULL OR numerator >= 0", name="ck_building_metric_facts_numerator"),
        CheckConstraint("denominator IS NULL OR denominator >= 0", name="ck_building_metric_facts_denominator"),
        CheckConstraint(
            "eligible_buildings >= 0 AND included_buildings >= 0 "
            "AND included_buildings <= eligible_buildings",
            name="ck_building_metric_facts_building_coverage",
        ),
        CheckConstraint(
            "covered_area_sqm IS NULL OR covered_area_sqm >= 0",
            name="ck_building_metric_facts_covered_area",
        ),
        CheckConstraint(
            "eligible_area_sqm IS NULL OR eligible_area_sqm >= 0",
            name="ck_building_metric_facts_eligible_area",
        ),
        CheckConstraint(
            "covered_area_sqm IS NULL OR eligible_area_sqm IS NULL OR covered_area_sqm <= eligible_area_sqm",
            name="ck_building_metric_facts_area_coverage",
        ),
        CheckConstraint(
            "eligible_days >= 0 AND covered_days >= 0 AND covered_days <= eligible_days",
            name="ck_building_metric_facts_day_coverage",
        ),
        CheckConstraint(
            "estimate_share IS NULL OR (estimate_share >= 0 AND estimate_share <= 1)",
            name="ck_building_metric_facts_estimate_share",
        ),
        CheckConstraint(
            "data_depth IN ('detail', 'snapshot', 'external')",
            name="ck_building_metric_facts_data_depth",
        ),
        CheckConstraint(
            "completeness_status IN ('unknown', 'partial', 'complete')",
            name="ck_building_metric_facts_completeness_status",
        ),
        CheckConstraint(
            "freshness_status IN ('unknown', 'current', 'stale')",
            name="ck_building_metric_facts_freshness_status",
        ),
        CheckConstraint(
            "validation_status IN ('pending', 'valid', 'rejected')",
            name="ck_building_metric_facts_validation_status",
        ),
        CheckConstraint(
            "publication_status IN ('draft', 'published', 'superseded')",
            name="ck_building_metric_facts_publication_status",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(
        Integer,
        ForeignKey("building_operating_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    metric_definition_id = Column(
        Integer,
        ForeignKey("metric_definitions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    value = Column(METRIC_VALUE, nullable=True)
    numerator = Column(METRIC_VALUE, nullable=True)
    denominator = Column(METRIC_VALUE, nullable=True)
    source_id = Column(Integer, ForeignKey("data_sources.id", ondelete="RESTRICT"), nullable=False, index=True)
    batch_id = Column(Integer, ForeignKey("import_batches.id", ondelete="RESTRICT"), nullable=True, index=True)
    source_record_id = Column(Integer, ForeignKey("source_records.id", ondelete="RESTRICT"), nullable=True, index=True)
    eligible_buildings = Column(Integer, nullable=False, default=1)
    included_buildings = Column(Integer, nullable=False, default=0)
    covered_area_sqm = Column(AREA, nullable=True)
    eligible_area_sqm = Column(AREA, nullable=True)
    covered_days = Column(Integer, nullable=False, default=0)
    eligible_days = Column(Integer, nullable=False, default=0)
    estimate_share = Column(Numeric(10, 6, asdecimal=False), nullable=True)
    estimate_method = Column(Text, nullable=False, default="")
    data_depth = Column(String(20), nullable=False)
    completeness_status = Column(String(20), nullable=False, default="unknown")
    freshness_status = Column(String(20), nullable=False, default="unknown")
    validation_status = Column(String(20), nullable=False, default="pending")
    publication_status = Column(String(20), nullable=False, default="draft")
    calculation_version = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)


class SystemConfig(Base):
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True)
    key = Column(String(50), unique=True)
    value = Column(Text)


class FinancialExpense(Base):
    __tablename__ = "financial_expenses"
    __table_args__ = (
        CheckConstraint("category IN ('opex', 'capex')", name="ck_financial_expenses_category"),
        CheckConstraint("amount_yuan >= 0", name="ck_financial_expenses_amount"),
        CheckConstraint("revision >= 1", name="ck_financial_expenses_revision"),
    )

    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    category = Column(String(10), nullable=False)
    amount_yuan = Column(MONEY, nullable=False)
    occurrence_month = Column(String(7), nullable=False, index=True)
    payment_month = Column(String(7), nullable=False, index=True)
    paid_on = Column(Date, nullable=True)
    owner = Column(String(100), nullable=False, default="")
    notes = Column(Text, nullable=False, default="")
    is_active = Column(Boolean, nullable=False, default=True)
    revision = Column(Integer, nullable=False, default=1)
    created_by = Column(String(100), nullable=False)
    updated_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)
    updated_at = Column(DateTime, nullable=False, default=now_in_timezone, onupdate=now_in_timezone)


class FinancialPlanVersion(Base):
    __tablename__ = "financial_plan_versions"
    __table_args__ = (
        UniqueConstraint("building_id", "year", "revision", name="uq_financial_plan_version_revision"),
        CheckConstraint("kind IN ('budget', 'forecast', 'scenario')", name="ck_financial_plan_versions_kind"),
        CheckConstraint("closed_through_month BETWEEN 0 AND 12", name="ck_financial_plan_versions_closed_month"),
        CheckConstraint("revision >= 1", name="ck_financial_plan_versions_revision"),
    )

    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    kind = Column(String(12), nullable=False)
    name = Column(String(100), nullable=False)
    base_version_id = Column(Integer, ForeignKey("financial_plan_versions.id", ondelete="RESTRICT"), nullable=True)
    closed_through_month = Column(Integer, nullable=False, default=0)
    client_state_json = Column(Text, nullable=False)
    snapshot_json = Column(Text, nullable=False)
    source_digest = Column(String(64), nullable=False)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)


class FinancialPlanningState(Base):
    __tablename__ = "financial_planning_states"
    __table_args__ = (
        UniqueConstraint("building_id", "year", name="uq_financial_planning_state_year"),
        CheckConstraint("revision >= 0", name="ck_financial_planning_states_revision"),
    )

    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    revision = Column(Integer, nullable=False, default=0)
    active_budget_id = Column(Integer, ForeignKey("financial_plan_versions.id", ondelete="RESTRICT"), nullable=True)
    active_forecast_id = Column(Integer, ForeignKey("financial_plan_versions.id", ondelete="RESTRICT"), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=now_in_timezone, onupdate=now_in_timezone)


class ExecutionAction(Base):
    """One durable execution record per contract expiry or fixed saved measure."""
    __tablename__ = 'execution_actions'
    __table_args__ = (
        UniqueConstraint('building_id', 'source_key', name='uq_execution_action_source'),
        ForeignKeyConstraint(['building_id','contract_id'], ['contracts.building_id','contracts.id'], name='fk_execution_scope_contract', ondelete='RESTRICT'),
        ForeignKeyConstraint(['building_id','result_contract_id'], ['contracts.building_id','contracts.id'], name='fk_execution_scope_result', ondelete='RESTRICT'),
        CheckConstraint("kind IN ('expiry','measure')", name='ck_execution_action_kind'),
        CheckConstraint("status IN ('open','waiting','done')", name='ck_execution_action_status'),
        CheckConstraint("decision IN ('undecided','renew','vacate')", name='ck_execution_action_decision'),
        CheckConstraint('revision >= 1', name='ck_execution_action_revision'),
        Index('ix_execution_actions_scope_due', 'building_id', 'status', 'due_on'),
    )
    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey('buildings.id', ondelete='RESTRICT'), nullable=False)
    source_key = Column(String(100), nullable=False)
    kind = Column(String(12), nullable=False)
    contract_id = Column(Integer, ForeignKey('contracts.id', ondelete='RESTRICT'))
    version_id = Column(Integer, ForeignKey('financial_plan_versions.id', ondelete='RESTRICT'))
    measure_key = Column(String(20))
    title = Column(String(200), nullable=False)
    source_json = Column(Text, nullable=False, default='{}')
    owner = Column(String(100), nullable=False)
    due_on = Column(Date, nullable=False)
    next_follow_up_on = Column(Date)
    status = Column(String(12), nullable=False, default='open')
    decision = Column(String(12), nullable=False, default='undecided')
    next_action = Column(Text, nullable=False)
    result_note = Column(Text, nullable=False, default='')
    result_contract_id = Column(Integer, ForeignKey('contracts.id', ondelete='RESTRICT'))
    follow_up_id = Column(Integer, ForeignKey('customer_follow_ups.id', ondelete='RESTRICT'))
    verification_json = Column(Text, nullable=False, default='{}')
    benefit_json = Column(Text, nullable=False, default='{}')
    revision = Column(Integer, nullable=False, default=1)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now_in_timezone)
    updated_at = Column(DateTime, nullable=False, default=now_in_timezone)
