from sqlalchemy import String, Text, DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.models.base import Base, gen_id


class AreaChunk(Base):
    __tablename__ = "area_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id"))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String, default="conversation")
    embedding: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AreaDocument(Base):
    __tablename__ = "area_documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("areas.id"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    filename: Mapped[str] = mapped_column(String)
    file_type: Mapped[str] = mapped_column(String)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
