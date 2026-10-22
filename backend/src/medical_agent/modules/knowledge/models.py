"""Versioned FAQ knowledge releases and searchable entries."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from medical_agent.core.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class KnowledgeRelease(Base):
    __tablename__ = "knowledge_releases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    source_name: Mapped[str] = mapped_column(String(255))
    source_checksum: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    quality_report_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    entries: Mapped[list["KnowledgeEntry"]] = relationship(
        back_populates="release",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    release_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_releases.id", ondelete="CASCADE"),
        index=True,
    )
    category: Mapped[str] = mapped_column(String(120))
    question: Mapped[str] = mapped_column(Text)
    aliases: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str] = mapped_column(Text)
    effective_date: Mapped[str] = mapped_column(String(80), default="")
    source_sheet: Mapped[str] = mapped_column(String(120))
    source_row: Mapped[int] = mapped_column(Integer)
    search_text: Mapped[str] = mapped_column(Text)
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    release: Mapped[KnowledgeRelease] = relationship(back_populates="entries")

    __table_args__ = (
        UniqueConstraint("release_id", "source_sheet", "source_row", name="uq_knowledge_source_row"),
        Index("ix_knowledge_release_category", "release_id", "category"),
    )


class KnowledgeOperation(Base):
    """Durable jobs, evaluations and audit events; no credentials are stored here."""

    __tablename__ = "knowledge_operations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    release_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    active_slot: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

