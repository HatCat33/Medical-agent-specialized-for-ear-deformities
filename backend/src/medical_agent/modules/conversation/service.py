"""Conversation and message use cases."""

import asyncio
from collections.abc import AsyncIterator
from weakref import WeakValueDictionary

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.core.config import get_settings
from medical_agent.core.database import session_factory
from medical_agent.modules.conversation import repository
from medical_agent.modules.conversation.models import Conversation, Message
from medical_agent.modules.safety.schemas import SafetyDecision
from medical_agent.modules.safety.service import evaluate_user_input
from medical_agent.providers.llm.client import ProviderError, build_chat_provider
from medical_agent.providers.llm.protocol import ChatTurn

SYSTEM_PROMPT = """你是耳畸形专科医疗健康科普助手。
仅提供一般健康科普和就医沟通建议，不进行诊断、畸形分型、手术判定、处方或用药剂量指导。
事实不确定时明确说明，不编造医院政策或患者个体结论。
发现紧急危险信号时优先建议立即联系急救服务或前往医院。
回答使用清晰、克制、易懂的中文，并说明内容不能替代医生面诊。
当前版本尚未接入医院知识库，不得声称回答来自医院资料。"""


# SQLite development deployments do not provide row-level locks. Serializing writes
# per conversation prevents duplicate sequence numbers until production moves to
# PostgreSQL, where the repository can replace this with SELECT ... FOR UPDATE.
_conversation_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _conversation_lock(conversation_id: str) -> asyncio.Lock:
    lock = _conversation_locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _conversation_locks[conversation_id] = lock
    return lock


async def _save_interrupted_message(
    *,
    conversation_id: str,
    content: str,
) -> None:
    """Save partial output outside the request's cancelled database session."""
    async with session_factory() as recovery_db:
        conversation = await recovery_db.get(Conversation, conversation_id)
        if conversation is None:
            return
        await repository.add_message(
            recovery_db,
            conversation=conversation,
            role="assistant",
            content=content,
            safety_code="generation_interrupted",
            status="interrupted",
        )
        await recovery_db.commit()


async def require_conversation(
    db: AsyncSession,
    *,
    conversation_id: str,
    owner_id: str,
) -> Conversation:
    conversation = await repository.get_conversation(
        db,
        conversation_id=conversation_id,
        owner_id=owner_id,
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation_not_found")
    return conversation


async def send_message(
    db: AsyncSession,
    *,
    conversation: Conversation,
    content: str,
) -> tuple[Message, Message]:
    async with _conversation_lock(conversation.id):
        user_message = await repository.add_message(
            db,
            conversation=conversation,
            role="user",
            content=content.strip(),
        )
        await repository.rename_from_first_message(conversation, content=content)

        decision = evaluate_user_input(content)
        if decision.action != "allow":
            assistant_content = decision.response or "当前问题无法由科普助手处理。"
        else:
            settings = get_settings()
            recent = await repository.list_messages(
                db,
                conversation_id=conversation.id,
                limit=settings.history_message_limit,
            )
            provider = build_chat_provider(settings)
            turns = [ChatTurn(role="system", content=SYSTEM_PROMPT)] + [
                ChatTurn(role=item.role, content=item.content) for item in recent
            ]
            try:
                assistant_content = await provider.complete(turns)
            except ProviderError:
                assistant_content = "服务暂时无法完成回答，请稍后重试。"
                decision = SafetyDecision(action="fallback", code="provider_unavailable")

        assistant_message = await repository.add_message(
            db,
            conversation=conversation,
            role="assistant",
            content=assistant_content,
            safety_code=decision.code,
        )
        await db.commit()
        await db.refresh(conversation)
        return user_message, assistant_message


async def stream_message(
    db: AsyncSession,
    *,
    conversation: Conversation,
    content: str,
) -> AsyncIterator[tuple[str, Message | str]]:
    """Persist a user turn, then yield model output as it arrives."""
    async with _conversation_lock(conversation.id):
        user_message = await repository.add_message(
            db,
            conversation=conversation,
            role="user",
            content=content.strip(),
        )
        await repository.rename_from_first_message(conversation, content=content)
        await db.commit()
        await db.refresh(user_message)
        yield "user_message", user_message

        decision = evaluate_user_input(content)
        chunks: list[str] = []
        assistant_saved = False
        assistant_status = "completed"

        try:
            if decision.action != "allow":
                assistant_content = decision.response or "当前问题无法由科普助手处理。"
                chunks.append(assistant_content)
                yield "delta", assistant_content
            else:
                settings = get_settings()
                recent = await repository.list_messages(
                    db,
                    conversation_id=conversation.id,
                    limit=settings.history_message_limit,
                )
                provider = build_chat_provider(settings)
                turns = [ChatTurn(role="system", content=SYSTEM_PROMPT)] + [
                    ChatTurn(role=item.role, content=item.content) for item in recent
                ]
                try:
                    async for chunk in provider.stream(turns):
                        chunks.append(chunk)
                        yield "delta", chunk
                except ProviderError:
                    fallback = (
                        "服务暂时无法完成回答，请稍后重试。"
                        if not chunks
                        else "\n\n回答生成中断，请稍后重试。"
                    )
                    chunks.append(fallback)
                    yield "delta", fallback
                    assistant_status = "interrupted"
                    decision = SafetyDecision(action="fallback", code="provider_unavailable")

            assistant_content = "".join(chunks).strip()
            if not assistant_content:
                assistant_content = "服务暂时没有返回有效内容，请稍后重试。"
                chunks.append(assistant_content)
                yield "delta", assistant_content
                assistant_status = "interrupted"
                decision = SafetyDecision(action="fallback", code="provider_empty")

            assistant_message = await repository.add_message(
                db,
                conversation=conversation,
                role="assistant",
                content=assistant_content,
                safety_code=decision.code,
                status=assistant_status,
            )
            await db.commit()
            await db.refresh(assistant_message)
            assistant_saved = True
            yield "done", assistant_message
        finally:
            # A disconnected browser should still retain the partial answer in history.
            if not assistant_saved and chunks:
                partial_content = "".join(chunks).strip()
                if partial_content:
                    recovery = asyncio.create_task(
                        _save_interrupted_message(
                            conversation_id=conversation.id,
                            content=partial_content,
                        )
                    )
                    try:
                        await asyncio.shield(recovery)
                    except asyncio.CancelledError:
                        # The recovery task remains alive after the response is cancelled.
                        pass

