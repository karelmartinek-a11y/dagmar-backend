from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class InstanceStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    DEACTIVATED = "DEACTIVATED"


class EmploymentTemplate(StrEnum):
    DPP_DPC = "DPP_DPC"
    HPP = "HPP"


class ClientType(StrEnum):
    ANDROID = "ANDROID"
    WEB = "WEB"


class PortalUserRole(StrEnum):
    EMPLOYEE = "employee"


class Instance(Base):
    __tablename__ = "instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID string

    client_type: Mapped[ClientType] = mapped_column(Enum(ClientType, name="client_type", create_type=False), nullable=False)
    device_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    device_info_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[InstanceStatus] = mapped_column(
        Enum(InstanceStatus, name="instance_status", create_type=False), nullable=False, default=InstanceStatus.PENDING
    )
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    profile_instance_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("instances.id", ondelete="SET NULL"), nullable=True
    )
    # volitelny odkaz na profilovou instanci
    profile_instance: Mapped[Instance | None] = relationship(
        "Instance",
        remote_side=[id],
        foreign_keys=[profile_instance_id],
    )
    shift_plans = relationship("ShiftPlan", back_populates="instance", cascade="all, delete-orphan")
    shift_plan_month_instances = relationship("ShiftPlanMonthInstance", back_populates="instance", cascade="all, delete-orphan")

    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    employment_template: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=EmploymentTemplate.DPP_DPC.value,
        server_default=EmploymentTemplate.DPP_DPC.value,
    )

    # Token is issued upon activation; store only a hash.
    token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attendances: Mapped[list[Attendance]] = relationship(
        back_populates="instance", cascade="all, delete-orphan", passive_deletes=True
    )
    attendance_locks: Mapped[list[AttendanceLock]] = relationship(
        back_populates="instance", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        Index("ix_instances_status", "status"),
        Index("ix_instances_last_seen_at", "last_seen_at"),
        Index("ix_instances_profile_instance_id", "profile_instance_id"),
    )


class Attendance(Base):
    __tablename__ = "attendance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    instance_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)

    # Stored as "HH:MM" or NULL. Validation is performed in API layer.
    arrival_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    departure_time: Mapped[str | None] = mapped_column(String(5), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    instance: Mapped[Instance] = relationship(back_populates="attendances")

    __table_args__ = (
        UniqueConstraint("instance_id", "date", name="uq_attendance_instance_date"),
        Index("ix_attendance_instance_date", "instance_id", "date"),
    )


class ShiftPlan(Base):
    __tablename__ = "shift_plan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    arrival_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    departure_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    instance: Mapped[Instance] = relationship("Instance", back_populates="shift_plans")

    __table_args__ = (
        UniqueConstraint("instance_id", "date", name="uq_shift_plan_instance_date"),
        Index("ix_shift_plan_instance_id", "instance_id"),
        Index("ix_shift_plan_date", "date"),
    )


class ShiftPlanMonthInstance(Base):
    __tablename__ = "shift_plan_month_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    instance_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    instance: Mapped[Instance] = relationship("Instance", back_populates="shift_plan_month_instances")

    __table_args__ = (
        UniqueConstraint("year", "month", "instance_id", name="uq_shift_plan_month_instance"),
        Index("ix_shift_plan_month_instances_year", "year"),
        Index("ix_shift_plan_month_instances_month", "month"),
        Index("ix_shift_plan_month_instances_instance_id", "instance_id"),
    )


class AttendanceLock(Base):
    __tablename__ = "attendance_locks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    instance: Mapped[Instance] = relationship(back_populates="attendance_locks")

    __table_args__ = (
        UniqueConstraint("instance_id", "year", "month", name="uq_attendance_lock_instance_month"),
        Index("ix_attendance_locks_instance_month", "instance_id", "year", "month"),
    )


class AdminUser(Base):
    """Single-admin setup.

    We keep a table to allow deterministic seed/update via scripts/seed_admin.sh.
    """

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PortalUser(Base):
    __tablename__ = "portal_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[PortalUserRole] = mapped_column(
        Enum(PortalUserRole, name="portal_user_role", create_type=False), nullable=False
    )
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    instance_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("instances.id", ondelete="SET NULL"), nullable=True
    )
    instance: Mapped[Instance | None] = relationship("Instance")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    reset_tokens: Mapped[list["PortalUserResetToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class PortalUserResetToken(Base):
    __tablename__ = "portal_user_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("portal_users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[PortalUser] = relationship(back_populates="reset_tokens")


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    afternoon_cutoff_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=17 * 60, server_default=str(17 * 60)
    )

    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_security: Mapped[str | None] = mapped_column(String(16), nullable=True)
    smtp_from_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
