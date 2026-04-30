from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Contract(Base):
    __tablename__ = "contracts"

    id = Column(Integer, primary_key=True, index=True)
    contract_no = Column(String(50), unique=True, index=True)
    tower = Column(String(10))
    room_no = Column(String(50), index=True)
    tenant_name = Column(String(100), index=True)
    area = Column(Float, default=0)
    unit_price = Column(Float, default=0)
    lease_type = Column(String(20))
    free_period = Column(Integer, default=0)
    monthly_rent = Column(Float, default=0)
    broker_fee = Column(Float, default=0)
    start_date = Column(Date)
    end_date = Column(Date)
    disposal = Column(String(20), default="")
    contact_person = Column(String(50), default="")
    contact_phone = Column(String(30), default="")
    contact_email = Column(String(100), default="")
    deposit = Column(Float, default=0)
    deposit_status = Column(String(20), default="")
    property_fee_rate = Column(Float, default=0)
    property_fee_free = Column(Integer, default=0)
    prev_contract_no = Column(String(50), default="")
    notes = Column(Text, default="")
    pdf_path = Column(String(500), default="")
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    logs = relationship("OperationLog", back_populates="contract")

    @property
    def monthly_property_fee(self) -> float:
        return round((self.area or 0) * (self.property_fee_rate or 0), 2)

    @property
    def net_property_fee(self) -> float:
        return self.monthly_property_fee

    @property
    def net_rent(self) -> float:
        total_months = self.total_months
        if total_months <= 0:
            return 0
        free = self.free_period or 0
        return round(max(0, (self.monthly_rent or 0) * (total_months - free) / total_months), 2)

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
        today = date.today()
        if not self.start_date or not self.end_date:
            return "信息不全"
        if today < self.start_date:
            return "待生效"
        if self.end_date < today:
            if self.disposal in {"已续租", "已退租", "逾期占用"}:
                return self.disposal
            return "已到期"
        if (self.end_date - today).days <= 90:
            return "即将到期"
        return "在租"

    @property
    def days_to_expiry(self) -> int | None:
        if not self.end_date:
            return None
        return (self.end_date - date.today()).days


class Room(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True, index=True)
    tower = Column(String(10))
    floor = Column(String(10))
    room_no = Column(String(50), unique=True, index=True)
    room_type = Column(String(20), default="办公")
    area = Column(Float, default=0)
    is_leasable = Column(Boolean, default=True)
    notes = Column(Text, default="")
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=True)
    action = Column(String(20))
    changes = Column(Text, default="")
    operator = Column(String(50), default="system")
    timestamp = Column(DateTime, default=datetime.now)

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
    created_at = Column(DateTime, default=datetime.now)


class SystemConfig(Base):
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True)
    key = Column(String(50), unique=True)
    value = Column(Text)


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    provider = Column(String(50), nullable=False)
    direction = Column(String(20), default="bidirectional")
    enabled = Column(Boolean, default=True)
    config_json = Column(Text, default="{}")
    secret_json = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class IntegrationEvent(Base):
    __tablename__ = "integration_events"

    id = Column(Integer, primary_key=True, index=True)
    connection_id = Column(Integer, ForeignKey("integration_connections.id"), nullable=True, index=True)
    event_type = Column(String(50), nullable=False)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(Integer, nullable=True)
    payload_json = Column(Text, default="{}")
    status = Column(String(20), default="pending", index=True)
    attempts = Column(Integer, default=0)
    last_error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class IntegrationSyncRun(Base):
    __tablename__ = "integration_sync_runs"

    id = Column(Integer, primary_key=True, index=True)
    connection_id = Column(Integer, ForeignKey("integration_connections.id"), nullable=False, index=True)
    direction = Column(String(20), default="bidirectional")
    status = Column(String(20), default="reserved")
    message = Column(Text, default="")
    started_at = Column(DateTime, default=datetime.now)
    finished_at = Column(DateTime, nullable=True)
