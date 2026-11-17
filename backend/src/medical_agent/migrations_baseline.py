"""Frozen schema for Alembic revision 0001. Do not edit for later schema changes."""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)


def baseline_metadata() -> MetaData:
    metadata = MetaData()
    Table(
        "conversations",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("owner_id", String(128), nullable=False, index=True),
        Column("title", String(120), nullable=False),
        Column("status", String(20), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        Column("last_message_at", DateTime(timezone=True)),
        Index("ix_conversations_owner_last", "owner_id", "last_message_at"),
    )
    Table(
        "messages",
        metadata,
        Column("id", String(36), primary_key=True),
        Column(
            "conversation_id",
            String(36),
            ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        Column("sequence_no", Integer, nullable=False),
        Column("role", String(20), nullable=False),
        Column("content", Text, nullable=False),
        Column("status", String(20), nullable=False),
        Column("safety_code", String(40)),
        Column("created_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("conversation_id", "sequence_no", name="uq_message_sequence"),
    )
    Table(
        "media_objects",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("owner_id", String(128), nullable=False, index=True),
        Column(
            "conversation_id",
            String(36),
            ForeignKey("conversations.id", ondelete="CASCADE"),
            index=True,
        ),
        Column("message_id", String(36), ForeignKey("messages.id", ondelete="CASCADE"), index=True),
        Column("kind", String(20), nullable=False),
        Column("original_name", String(255), nullable=False),
        Column("media_type", String(80), nullable=False),
        Column("size_bytes", Integer, nullable=False),
        Column("storage_key", String(512), nullable=False, unique=True),
        Column("status", String(20), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Index("ix_media_owner_status_created", "owner_id", "status", "created_at"),
    )
    Table(
        "knowledge_releases",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("name", String(120), nullable=False),
        Column("source_name", String(255), nullable=False),
        Column("source_checksum", String(64), nullable=False, unique=True),
        Column("status", String(20), nullable=False, index=True),
        Column("entry_count", Integer, nullable=False),
        Column("quality_report_json", Text, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("published_at", DateTime(timezone=True)),
    )
    Table(
        "knowledge_entries",
        metadata,
        Column("id", String(36), primary_key=True),
        Column(
            "release_id",
            String(36),
            ForeignKey("knowledge_releases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        Column("category", String(120), nullable=False),
        Column("question", Text, nullable=False),
        Column("aliases", Text, nullable=False),
        Column("answer", Text, nullable=False),
        Column("effective_date", String(80), nullable=False),
        Column("source_sheet", String(120), nullable=False),
        Column("source_row", Integer, nullable=False),
        Column("search_text", Text, nullable=False),
        Column("embedding_json", Text),
        Column("created_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint(
            "release_id", "source_sheet", "source_row", name="uq_knowledge_source_row"
        ),
        Index("ix_knowledge_release_category", "release_id", "category"),
    )
    Table(
        "knowledge_operations",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("release_id", String(36), nullable=False, index=True),
        Column("kind", String(40), nullable=False, index=True),
        Column("status", String(20), nullable=False),
        Column("active_slot", String(36), unique=True),
        Column("processed", Integer, nullable=False),
        Column("total", Integer, nullable=False),
        Column("detail_json", Text, nullable=False),
        Column("error", String(255), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("finished_at", DateTime(timezone=True)),
    )
    return metadata
