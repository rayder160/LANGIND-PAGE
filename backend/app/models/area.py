from sqlalchemy import String, Text, DateTime, ForeignKey, Integer, Float, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from app.models.base import Base, gen_id


class Area(Base):
    __tablename__ = "areas"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String)
    memory: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    memory_recent: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    memory_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # CME — campos del Cognitive Memory Engine
    cme_lambda_rate: Mapped[float] = mapped_column(Float, default=0.01)                          # tasa de olvido configurable por área
    last_pattern_detection_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # evita detección redundante
    episode_count_since_last_detection: Mapped[int] = mapped_column(Integer, default=0)          # trigger cada 10 episodios
    consolidation_window_start: Mapped[str | None] = mapped_column(String, nullable=True)        # hora inicio consolidación nocturna
    consolidation_window_end: Mapped[str | None] = mapped_column(String, nullable=True)          # hora fin consolidación nocturna

    tenant: Mapped["Tenant"] = relationship(back_populates="areas")
    users: Mapped[list["User"]] = relationship(back_populates="area")
