"""Conversation history persistence operations."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from medical_agent.modules.conversation.models import Conversation, Message


def utc_now() -> datetime:
    return datetime.now(UTC)


async def create_conversation(
    db: AsyncSession,
    *,
    owner_id: str,
    title: str = "新会话",
) -> Conversation:
    conversation = Conversation(id=str(uuid.uuid4()), owner_id=owner_id, title=title)
    db.add(conversation)
    await db.flush()
    return conversation


async def list_conversations(db: AsyncSession, *, owner_id: str) -> list[Conversation]:
    result = await db.scalars(
        select(Conversation)
        .where(Conversation.owner_id == owner_id, Conversation.status != "deleted")
        .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.created_at.desc())
    )
    return list(result)


async def get_conversation(
    db: AsyncSession,
    *,
    conversation_id: str,
    owner_id: str,
) -> Conversation | None:
    return await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.owner_id == owner_id,
            Conversation.status != "deleted",
        )
    )


async def list_messages(
    db: AsyncSession,
    *,
    conversation_id: str,
    limit: int | None = None,
) -> list[Message]:
    order = Message.sequence_no.desc() if limit else Message.sequence_no.asc()
    statement = (
        select(Message)
        .options(selectinload(Message.attachments))
        .where(Message.conversation_id == conversation_id)
        .order_by(order)
    )
    if limit:
        statement = statement.limit(limit)
    messages = list(await db.scalars(statement))
    return list(reversed(messages)) if limit else messages


async def add_message(
    db: AsyncSession,
    *,
    conversation: Conversation,
    role: str,
    content: str,
    safety_code: str | None = None,
    status: str = "completed",
) -> Message:
    current_sequence = await db.scalar(
        select(func.max(Message.sequence_no)).where(Message.conversation_id == conversation.id)
    )
    now = utc_now()
    message = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation.id,
        sequence_no=(current_sequence or 0) + 1,
        role=role,
        content=content,
        status=status,
        safety_code=safety_code,
        attachments=[],
    )
    conversation.last_message_at = now
    conversation.updated_at = now
    db.add(message)
    await db.flush()
    return message


async def rename_from_first_message(conversation: Conversation, *, content: str) -> None:
    if conversation.title != "新会话":
        return
    normalized = " ".join(content.split())
    conversation.title = normalized[:32] + ("..." if len(normalized) > 32 else "")


async def delete_conversation(
    db: AsyncSession,
    *,
    conversation_id: str,
    owner_id: str,
) -> bool:
    conversation_exists = await db.scalar(
        select(Conversation.id).where(
            Conversation.id == conversation_id,
            Conversation.owner_id == owner_id,
        )
    )
    if conversation_exists is None:
        return False

    # Do not rely solely on database-level cascades: SQLite foreign keys can be
    # disabled by the host process, while message history must never be orphaned.
    await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
    await db.execute(
        delete(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.owner_id == owner_id,
        )
    )
    return True

