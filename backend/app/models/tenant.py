from sqlalchemy import String, Integer, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from app.models.base import Base, gen_id, gen_api_key, trial_end


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    name: Mapped[str] = mapped_column(String, unique=True)
    api_key: Mapped[str] = mapped_column(String, unique=True, default=gen_api_key)
    licenses_total: Mapped[int] = mapped_column(Integer, default=5)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    subscription_status: Mapped[str] = mapped_column(String, default="trial")
    billing_cycle: Mapped[str] = mapped_column(String, default="monthly")
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=trial_end)
    subscription_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="tenant", cascade="all, delete")
    areas: Mapped[list["Area"]] = relationship(back_populates="tenant", cascade="all, delete")
