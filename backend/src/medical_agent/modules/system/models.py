"""Database models for prompts and administrative runtime operations."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from medical_agent.core.database import Base
from medical_agent.modules.safety.prompt import SAFETY_SYSTEM_PROMPT


def utc_now() -> datetime:
    return datetime.now(UTC)


class PromptRelease(Base):
    __tablename__ = "prompt_releases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text)
    system_content: Mapped[str] = mapped_column(Text, default=SAFETY_SYSTEM_PROMPT)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AdminOperation(Base):
    """Audit events and model probe results without secrets or provider response bodies."""

    __tablename__ = "admin_operations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    target_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)
