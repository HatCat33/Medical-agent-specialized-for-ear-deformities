"""Conversation and message use cases."""

import asyncio
from collections.abc import AsyncIterator
from weakref import WeakValueDictionary

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.core.config import Settings, get_settings
from medical_agent.core.database import session_factory
from medical_agent.core.logging import get_logger
from medical_agent.modules.conversation import repository
from medical_agent.modules.conversation.models import Conversation, Message
from medical_agent.modules.media import service as media_service
from medical_agent.modules.media.models import MediaObject
from medical_agent.modules.safety.schemas import SafetyDecision
from medical_agent.modules.safety.service import evaluate_user_input
from medical_agent.providers.llm.client import ProviderError, build_chat_provider
from medical_agent.providers.llm.protocol import ChatTurn, ImageInput

logger = get_logger(__name__)

SYSTEM_PROMPT = """你是耳畸形专科医疗健康科普助手。
仅提供一般健康科普和就医沟通建议，不进行诊断、畸形分型、手术判定、处方或用药剂量指导。
处理图片时，只能客观描述与科普相关的可见信息；不得根据图片确认疾病、畸形类型、严重程度、手术方案或预后。
不要识别或猜测图中人物身份。图片不清晰、角度不足或信息不足时，应明确说明局限并建议由医生面诊评估。
事实不确定时明确说明，不编造医院政策或患者个体结论。
发现紧急危险信号时优先建议立即联系急救服务或前往医院。
回答使用清晰、克制、易懂的中文，并说明内容不能替代医生面诊。
当前版本尚未接入医院知识库，不得声称回答来自医院资料。"""

IMAGE_ONLY_QUESTION = "请对上传图片中与耳部健康科普相关的可见信息进行客观说明。"

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


def _chat_turns(
    recent: list[Message],
    *,
    current_message_id: str,
    images: tuple[ImageInput, ...],
) -> list[ChatTurn]:
    turns = [ChatTurn(role="system", content=SYSTEM_PROMPT)]
    turns.extend(
        ChatTurn(
            role=item.role,
            content=item.content,
            images=images if item.id == current_message_id else (),
        )
        for item in recent
    )
    return turns


async def _prepare_user_message(
    db: AsyncSession,
    *,
    conversation: Conversation,
    content: str,
    attachment_ids: list[str],
    input_mode: str,
    settings: Settings,
) -> tuple[Message, list[MediaObject], str]:
    normalized_content = content.strip() or IMAGE_ONLY_QUESTION
    user_message = await repository.add_message(
        db,
        conversation=conversation,
        role="user",
        content=normalized_content,
    )
    attachments = await media_service.claim_media(
        db,
        media_ids=attachment_ids,
        owner_id=conversation.owner_id,
        conversation_id=conversation.id,
        message=user_message,
        settings=settings,
    )
    await repository.rename_from_first_message(conversation, content=normalized_content)
    logger.info(
        "conversation_user_message_created",
        conversation_id=conversation.id,
        message_id=user_message.id,
        input_mode=input_mode,
        attachment_count=len(attachments),
        content_length=len(normalized_content),
    )
    return user_message, attachments, normalized_content


async def _save_interrupted_message(*, conversation_id: str, content: str) -> None:
    """Save partial output outside the request's cancelled database session."""
    async with session_factory() as recovery_db:
        conversation = await recovery_db.get(Conversation, conversation_id)
        if conversation is None:
            return
        message = await repository.add_message(
            recovery_db,
            conversation=conversation,
            role="assistant",
            content=content,
            safety_code="generation_interrupted",
            status="interrupted",
        )
        await recovery_db.commit()
        logger.info(
            "conversation_partial_answer_saved",
            conversation_id=conversation_id,
            message_id=message.id,
            content_length=len(content),
        )


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
    attachment_ids: list[str] | None = None,
    input_mode: str = "text",
) -> tuple[Message, Message]:
    async with _conversation_lock(conversation.id):
        settings = get_settings()
        user_message, attachments, normalized_content = await _prepare_user_message(
            db,
            conversation=conversation,
            content=content,
            attachment_ids=attachment_ids or [],
            input_mode=input_mode,
            settings=settings,
        )
        images = await media_service.load_image_inputs(attachments, settings=settings)

        decision = evaluate_user_input(normalized_content)
        if decision.action != "allow":
            assistant_content = decision.response or "当前问题无法由科普助手处理。"
        else:
            recent = await repository.list_messages(
                db,
                conversation_id=conversation.id,
                limit=settings.history_message_limit,
            )
            provider = build_chat_provider(settings)
            try:
                assistant_content = await provider.complete(
                    _chat_turns(recent, current_message_id=user_message.id, images=images)
                )
            except ProviderError:
                logger.warning(
                    "model_request_failed",
                    conversation_id=conversation.id,
                    message_id=user_message.id,
                )
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
        logger.info(
            "conversation_answer_completed",
            conversation_id=conversation.id,
            message_id=assistant_message.id,
            status=assistant_message.status,
            safety_code=assistant_message.safety_code,
            content_length=len(assistant_content),
        )
        return user_message, assistant_message


async def stream_message(
    db: AsyncSession,
    *,
    conversation: Conversation,
    content: str,
    attachment_ids: list[str] | None = None,
    input_mode: str = "text",
) -> AsyncIterator[tuple[str, Message | str]]:
    """Persist a user turn and its media, then yield model output as it arrives."""
    async with _conversation_lock(conversation.id):
        settings = get_settings()
        user_message, attachments, normalized_content = await _prepare_user_message(
            db,
            conversation=conversation,
            content=content,
            attachment_ids=attachment_ids or [],
            input_mode=input_mode,
            settings=settings,
        )
        images = await media_service.load_image_inputs(attachments, settings=settings)
        await db.commit()
        await db.refresh(user_message)
        yield "user_message", user_message

        decision = evaluate_user_input(normalized_content)
        chunks: list[str] = []
        assistant_saved = False
        assistant_status = "completed"

        try:
            if decision.action != "allow":
                assistant_content = decision.response or "当前问题无法由科普助手处理。"
                chunks.append(assistant_content)
                yield "delta", assistant_content
            else:
                recent = await repository.list_messages(
                    db,
                    conversation_id=conversation.id,
                    limit=settings.history_message_limit,
                )
                provider = build_chat_provider(settings)
                try:
                    async for chunk in provider.stream(
                        _chat_turns(recent, current_message_id=user_message.id, images=images)
                    ):
                        chunks.append(chunk)
                        yield "delta", chunk
                except ProviderError as exc:
                    logger.warning(
                        "model_stream_failed",
                        conversation_id=conversation.id,
                        message_id=user_message.id,
                        input_type="vision" if images else "text",
                        reason=str(exc),
                        upstream_status=exc.status_code,
                        partial_content_length=sum(len(chunk) for chunk in chunks),
                    )
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
            logger.info(
                "conversation_answer_completed",
                conversation_id=conversation.id,
                message_id=assistant_message.id,
                status=assistant_message.status,
                safety_code=assistant_message.safety_code,
                content_length=len(assistant_content),
            )
            yield "done", assistant_message
        finally:
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
                        pass
