from sqlalchemy import String, Text, DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.models.base import Base, gen_id


class UserAnalytics(Base):
    __tablename__ = "user_analytics"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    area_id: Mapped[str | None] = mapped_column(ForeignKey("areas.id"), nullable=True)

    total_messages: Mapped[int] = mapped_column(Integer, default=0)
    total_sessions: Mapped[int] = mapped_column(Integer, default=0)
    avg_message_length: Mapped[float] = mapped_column(default=0.0)
    clarification_requests: Mapped[int] = mapped_column(Integer, default=0)
    rephrased_questions: Mapped[int] = mapped_column(Integer, default=0)
    topic_frequency: Mapped[str | None] = mapped_column(Text, nullable=True, default="{}")
    last_active: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_active: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_days: Mapped[int] = mapped_column(Integer, default=0)
    negative_sentiment_count: Mapped[int] = mapped_column(Integer, default=0)
    positive_sentiment_count: Mapped[int] = mapped_column(Integer, default=0)
    frustration_alerts: Mapped[int] = mapped_column(Integer, default=0)
    thumbs_up: Mapped[int] = mapped_column(Integer, default=0)
    thumbs_down: Mapped[int] = mapped_column(Integer, default=0)
    conversation_quality_score: Mapped[float] = mapped_column(default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AreaActivityLog(Base):
    __tablename__ = "area_activity_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    hour: Mapped[int] = mapped_column(Integer)
    weekday: Mapped[int] = mapped_column(Integer)
    message_count: Mapped[int] = mapped_column(Integer, default=1)
    date: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MessageFeedback(Base):
    __tablename__ = "message_feedback"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    message_id: Mapped[str] = mapped_column(ForeignKey("chat_messages.id"))
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    area_id: Mapped[str | None] = mapped_column(ForeignKey("areas.id"), nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    rating: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
