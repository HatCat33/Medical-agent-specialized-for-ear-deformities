"""Conversation and message use cases."""

import asyncio
import hashlib
import time
import uuid
from collections.abc import AsyncIterator
from weakref import WeakValueDictionary

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.core.config import Settings, get_settings
from medical_agent.core.database import session_factory
from medical_agent.core.logging import get_interaction_logger, get_logger
from medical_agent.modules.conversation import repository
from medical_agent.modules.conversation.models import Conversation, Message
from medical_agent.modules.knowledge import service as knowledge_service
from medical_agent.modules.knowledge.schemas import RetrievalHit
from medical_agent.modules.media import service as media_service
from medical_agent.modules.media.models import MediaObject
from medical_agent.modules.safety.schemas import SafetyDecision
from medical_agent.modules.safety.service import evaluate_user_input
from medical_agent.modules.system import prompt_service
from medical_agent.providers.llm.client import ProviderError, build_chat_provider
from medical_agent.providers.llm.protocol import ChatTurn, ImageInput

logger = get_logger(__name__)
interaction_logger = get_interaction_logger()

IMAGE_ONLY_QUESTION = "请对上传图片中与耳部健康科普相关的可见信息进行客观说明。"
RAG_NO_MATCH_RESPONSE = (
    "当前已发布的医院知识库中没有检索到足够相关的内容。"
    "为避免提供不可靠的信息，我暂不根据通用知识补充回答。建议记录问题并在就诊时咨询医生。"
)

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


def _content_log_fields(
    *,
    input_text: str,
    output_text: str,
    settings: Settings,
) -> dict[str, object]:
    fields: dict[str, object] = {
        "input_length": len(input_text),
        "output_length": len(output_text),
        "input_sha256": hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
        "output_sha256": hashlib.sha256(output_text.encode("utf-8")).hexdigest(),
    }
    if not settings.interaction_log_include_content:
        return fields

    limit = settings.interaction_log_max_content_chars
    fields.update(
        input_text=input_text[:limit],
        output_text=output_text[:limit],
        input_truncated=len(input_text) > limit,
        output_truncated=len(output_text) > limit,
    )
    return fields


def _record_interaction(
    *,
    interaction_id: str,
    conversation: Conversation,
    user_message: Message,
    assistant_message_id: str | None,
    input_text: str,
    output_text: str,
    input_mode: str,
    attachments: list[MediaObject],
    knowledge_hits: list[RetrievalHit],
    settings: Settings,
    status: str,
    safety_code: str | None,
    duration_ms: float,
    transport: str,
    prompt_release_id: str | None,
    prompt_checksum: str,
    error_code: str | None = None,
) -> None:
    if not settings.interaction_log_enabled:
        return
    has_images = bool(attachments)
    model_name = settings.vision_model if has_images else settings.llm_model
    fields: dict[str, object] = {
        "interaction_id": interaction_id,
        "conversation_id": conversation.id,
        "owner_ref": hashlib.sha256(conversation.owner_id.encode("utf-8")).hexdigest()[:16],
        "user_message_id": user_message.id,
        "assistant_message_id": assistant_message_id,
        "transport": transport,
        "input_mode": input_mode,
        "attachment_count": len(attachments),
        "attachment_ids": [attachment.id for attachment in attachments],
        "media_types": [attachment.media_type for attachment in attachments],
        "provider": settings.llm_provider,
        "model": model_name or ("demo" if settings.llm_provider == "demo" else "unconfigured"),
        "prompt_release_id": prompt_release_id,
        "prompt_checksum": prompt_checksum,
        "rag_enabled": settings.rag_enabled,
        "rag_hit_count": len(knowledge_hits),
        "rag_hits": [
            {
                "entry_id": hit.entry_id,
                "source_name": hit.source_name,
                "source_sheet": hit.source_sheet,
                "source_row": hit.source_row,
                "category": hit.category,
                "score": hit.score,
            }
            for hit in knowledge_hits
        ],
        "status": status,
        "safety_code": safety_code,
        "error_code": error_code,
        "duration_ms": round(duration_ms, 2),
        **_content_log_fields(
            input_text=input_text,
            output_text=output_text,
            settings=settings,
        ),
    }
    try:
        interaction_logger.info("conversation_interaction", **fields)
    except Exception:
        logger.exception(
            "interaction_log_write_failed",
            interaction_id=interaction_id,
            conversation_id=conversation.id,
        )


def _chat_turns(
    recent: list[Message],
    *,
    current_message_id: str,
    images: tuple[ImageInput, ...],
    business_prompt: str,
    knowledge_context: str = "",
) -> list[ChatTurn]:
    system_content = prompt_service.assemble_system_prompt(business_prompt, knowledge_context)
    turns = [ChatTurn(role="system", content=system_content)]
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
        started = time.perf_counter()
        interaction_id = str(uuid.uuid4())
        settings = get_settings()
        business_prompt, prompt_release_id, prompt_checksum = (
            await prompt_service.current_runtime_prompt(db)
        )
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
        knowledge_hits: list[RetrievalHit] = []
        interaction_error: str | None = None
        if decision.action != "allow":
            assistant_content = decision.response or "当前问题无法由科普助手处理。"
        else:
            knowledge_context = ""
            if settings.rag_enabled:
                knowledge_context, knowledge_hits = await knowledge_service.retrieve_context(
                    db,
                    query=normalized_content,
                    settings=settings,
                )
            recent = await repository.list_messages(
                db,
                conversation_id=conversation.id,
                limit=settings.history_message_limit,
            )
            provider = build_chat_provider(settings)
            if settings.rag_enabled and settings.rag_require_match and not knowledge_hits and not images:
                assistant_content = RAG_NO_MATCH_RESPONSE
                decision = SafetyDecision(action="fallback", code="knowledge_not_found")
            else:
                try:
                    assistant_content = await provider.complete(
                        _chat_turns(
                            recent,
                            current_message_id=user_message.id,
                            images=images,
                            business_prompt=business_prompt,
                            knowledge_context=knowledge_context,
                        )
                    )
                    if knowledge_hits:
                        assistant_content += knowledge_service.citation_markdown(knowledge_hits)
                except ProviderError as exc:
                    interaction_error = str(exc)
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
        _record_interaction(
            interaction_id=interaction_id,
            conversation=conversation,
            user_message=user_message,
            assistant_message_id=assistant_message.id,
            input_text=normalized_content,
            output_text=assistant_content,
            input_mode=input_mode,
            attachments=attachments,
            knowledge_hits=knowledge_hits,
            settings=settings,
            status=assistant_message.status,
            safety_code=assistant_message.safety_code,
            duration_ms=(time.perf_counter() - started) * 1000,
            transport="request_response",
            prompt_release_id=prompt_release_id,
            prompt_checksum=prompt_checksum,
            error_code=interaction_error,
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
        started = time.perf_counter()
        interaction_id = str(uuid.uuid4())
        settings = get_settings()
        business_prompt, prompt_release_id, prompt_checksum = (
            await prompt_service.current_runtime_prompt(db)
        )
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
        knowledge_hits: list[RetrievalHit] = []
        assistant_saved = False
        assistant_status = "completed"
        interaction_error: str | None = None

        try:
            if decision.action != "allow":
                assistant_content = decision.response or "当前问题无法由科普助手处理。"
                chunks.append(assistant_content)
                yield "delta", assistant_content
            else:
                knowledge_context = ""
                if settings.rag_enabled:
                    knowledge_context, knowledge_hits = await knowledge_service.retrieve_context(
                        db,
                        query=normalized_content,
                        settings=settings,
                    )
                recent = await repository.list_messages(
                    db,
                    conversation_id=conversation.id,
                    limit=settings.history_message_limit,
                )
                provider = build_chat_provider(settings)
                if settings.rag_enabled and settings.rag_require_match and not knowledge_hits and not images:
                    chunks.append(RAG_NO_MATCH_RESPONSE)
                    yield "delta", RAG_NO_MATCH_RESPONSE
                    decision = SafetyDecision(action="fallback", code="knowledge_not_found")
                else:
                    try:
                        async for chunk in provider.stream(
                            _chat_turns(
                                recent,
                                current_message_id=user_message.id,
                                images=images,
                                business_prompt=business_prompt,
                                knowledge_context=knowledge_context,
                            )
                        ):
                            chunks.append(chunk)
                            yield "delta", chunk
                    except ProviderError as exc:
                        interaction_error = str(exc)
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

                if knowledge_hits and decision.action == "allow" and chunks:
                    citation = knowledge_service.citation_markdown(knowledge_hits)
                    chunks.append(citation)
                    yield "delta", citation

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
            _record_interaction(
                interaction_id=interaction_id,
                conversation=conversation,
                user_message=user_message,
                assistant_message_id=assistant_message.id,
                input_text=normalized_content,
                output_text=assistant_content,
                input_mode=input_mode,
                attachments=attachments,
                knowledge_hits=knowledge_hits,
                settings=settings,
                status=assistant_message.status,
                safety_code=assistant_message.safety_code,
                duration_ms=(time.perf_counter() - started) * 1000,
                transport="sse",
                prompt_release_id=prompt_release_id,
                prompt_checksum=prompt_checksum,
                error_code=interaction_error,
            )
            yield "done", assistant_message
        finally:
            if not assistant_saved:
                partial_content = "".join(chunks).strip()
                _record_interaction(
                    interaction_id=interaction_id,
                    conversation=conversation,
                    user_message=user_message,
                    assistant_message_id=None,
                    input_text=normalized_content,
                    output_text=partial_content,
                    input_mode=input_mode,
                    attachments=attachments,
                    knowledge_hits=knowledge_hits,
                    settings=settings,
                    status="interrupted" if partial_content else "failed",
                    safety_code=decision.code,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    transport="sse",
                    prompt_release_id=prompt_release_id,
                    prompt_checksum=prompt_checksum,
                    error_code=interaction_error or "generation_not_completed",
                )
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
