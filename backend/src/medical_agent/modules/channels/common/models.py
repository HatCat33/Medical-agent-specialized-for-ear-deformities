"""Persistent channel event receipts used for callback deduplication."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from medical_agent.core.database import Base


class ChannelEventReceipt(Base):
    __tablename__ = "channel_event_receipts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    channel: Mapped[str] = mapped_column(String(24), index=True)
    event_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint("channel", "event_id", name="uq_channel_event_receipt"),
    )


class ChannelSyncCursor(Base):
    __tablename__ = "channel_sync_cursors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    channel: Mapped[str] = mapped_column(String(24), index=True)
    scope: Mapped[str] = mapped_column(String(128))
    cursor: Mapped[str] = mapped_column(String(256), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint("channel", "scope", name="uq_channel_sync_cursor"),
    )
