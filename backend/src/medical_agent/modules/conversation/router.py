"""Conversation, message, and history endpoints."""

import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.api.dependencies import current_owner_id
from medical_agent.core.database import get_db
from medical_agent.modules.conversation import repository, service
from medical_agent.modules.conversation.models import Message
from medical_agent.modules.conversation.schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    MessageRead,
    SendMessageRequest,
    SendMessageResponse,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])
DbSession = Annotated[AsyncSession, Depends(get_db)]
OwnerId = Annotated[str, Depends(current_owner_id)]


@router.get("", response_model=list[ConversationRead])
async def list_conversations(db: DbSession, owner_id: OwnerId) -> list[ConversationRead]:
    items = await repository.list_conversations(db, owner_id=owner_id)
    return [ConversationRead.model_validate(item) for item in items]


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    db: DbSession,
    owner_id: OwnerId,
) -> ConversationRead:
    conversation = await repository.create_conversation(
        db,
        owner_id=owner_id,
        title=(payload.title or "新会话").strip() or "新会话",
    )
    await db.commit()
    await db.refresh(conversation)
    return ConversationRead.model_validate(conversation)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    db: DbSession,
    owner_id: OwnerId,
) -> ConversationDetail:
    conversation = await service.require_conversation(
        db,
        conversation_id=conversation_id,
        owner_id=owner_id,
    )
    messages = await repository.list_messages(db, conversation_id=conversation.id)
    return ConversationDetail(
        **ConversationRead.model_validate(conversation).model_dump(),
        messages=[MessageRead.model_validate(message) for message in messages],
    )


@router.post("/{conversation_id}/messages", response_model=SendMessageResponse)
async def send_message(
    conversation_id: str,
    payload: SendMessageRequest,
    db: DbSession,
    owner_id: OwnerId,
) -> SendMessageResponse:
    conversation = await service.require_conversation(
        db,
        conversation_id=conversation_id,
        owner_id=owner_id,
    )
    user_message, assistant_message = await service.send_message(
        db,
        conversation=conversation,
        content=payload.content,
    )
    return SendMessageResponse(
        conversation=ConversationRead.model_validate(conversation),
        user_message=MessageRead.model_validate(user_message),
        assistant_message=MessageRead.model_validate(assistant_message),
    )


@router.post("/{conversation_id}/messages/stream", response_class=StreamingResponse)
async def stream_message(
    conversation_id: str,
    payload: SendMessageRequest,
    db: DbSession,
    owner_id: OwnerId,
) -> StreamingResponse:
    conversation = await service.require_conversation(
        db,
        conversation_id=conversation_id,
        owner_id=owner_id,
    )

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event, value in service.stream_message(
                db,
                conversation=conversation,
                content=payload.content,
            ):
                if event in {"user_message", "done"}:
                    if not isinstance(value, Message):
                        raise TypeError("stream_message_event_invalid")
                    data = {"message": MessageRead.model_validate(value).model_dump(mode="json")}
                else:
                    data = {"content": value}
                encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                yield f"event: {event}\ndata: {encoded}\n\n"
        # Once streaming starts, failures must be reported inside the event stream.
        except Exception:  # noqa: BLE001
            await db.rollback()
            encoded = json.dumps(
                {"message": "消息生成失败，请稍后重试。"},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield f"event: error\ndata: {encoded}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    db: DbSession,
    owner_id: OwnerId,
) -> Response:
    deleted = await repository.delete_conversation(
        db,
        conversation_id=conversation_id,
        owner_id=owner_id,
    )
    if not deleted:
        await service.require_conversation(
            db,
            conversation_id=conversation_id,
            owner_id=owner_id,
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

