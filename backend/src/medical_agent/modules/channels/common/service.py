"""Channel identity and deterministic conversation resolution."""

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.modules.channels.common.models import ChannelEventReceipt, ChannelSyncCursor
from medical_agent.modules.conversation import repository
from medical_agent.modules.conversation.models import Conversation


def channel_owner_id(*, channel: str, bot_id: str, chat_scope: str) -> str:
    digest = hashlib.sha256(f"{channel}:{bot_id}:{chat_scope}".encode()).hexdigest()[:40]
    return f"{channel}:{digest}"


def channel_conversation_id(*, channel: str, bot_id: str, chat_scope: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"medical-agent:{channel}:{bot_id}:{chat_scope}"))


async def get_or_create_conversation(
    db: AsyncSession,
    *,
    channel: str,
    bot_id: str,
    chat_scope: str,
    title: str,
) -> Conversation:
    owner_id = channel_owner_id(channel=channel, bot_id=bot_id, chat_scope=chat_scope)
    conversation_id = channel_conversation_id(
        channel=channel,
        bot_id=bot_id,
        chat_scope=chat_scope,
    )
    conversation = await repository.get_conversation(
        db,
        conversation_id=conversation_id,
        owner_id=owner_id,
    )
    if conversation is not None:
        return conversation
    return await repository.create_conversation(
        db,
        owner_id=owner_id,
        title=title,
        conversation_id=conversation_id,
    )


async def claim_event(db: AsyncSession, *, channel: str, event_id: str) -> bool:
    db.add(ChannelEventReceipt(channel=channel, event_id=event_id))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return False
    return True


async def release_event(db: AsyncSession, *, channel: str, event_id: str) -> None:
    await db.execute(
        delete(ChannelEventReceipt).where(
            ChannelEventReceipt.channel == channel,
            ChannelEventReceipt.event_id == event_id,
        )
    )
    await db.commit()


async def get_sync_cursor(db: AsyncSession, *, channel: str, scope: str) -> str:
    value = await db.scalar(
        select(ChannelSyncCursor.cursor).where(
            ChannelSyncCursor.channel == channel,
            ChannelSyncCursor.scope == scope,
        )
    )
    return value or ""


async def save_sync_cursor(
    db: AsyncSession,
    *,
    channel: str,
    scope: str,
    cursor: str,
) -> None:
    row = await db.scalar(
        select(ChannelSyncCursor).where(
            ChannelSyncCursor.channel == channel,
            ChannelSyncCursor.scope == scope,
        )
    )
    if row is None:
        db.add(ChannelSyncCursor(channel=channel, scope=scope, cursor=cursor))
    else:
        row.cursor = cursor
        row.updated_at = datetime.now(UTC)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        row = await db.scalar(
            select(ChannelSyncCursor).where(
                ChannelSyncCursor.channel == channel,
                ChannelSyncCursor.scope == scope,
            )
        )
        if row is None:
            raise
        row.cursor = cursor
        row.updated_at = datetime.now(UTC)
        await db.commit()

