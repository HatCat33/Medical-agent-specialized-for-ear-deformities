"""Versioned system/business prompt management and runtime assembly."""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.core.logging import get_logger
from medical_agent.modules.safety.prompt import SAFETY_SYSTEM_PROMPT
from medical_agent.modules.system.models import AdminOperation, PromptRelease

logger = get_logger(__name__)

DEFAULT_BUSINESS_PROMPT = """
你是耳畸形专科医疗健康科普助手。
回答使用清晰、克制、易懂的中文。优先依据已提供的医院知识库上下文回答；资料不足时直接说明信息局限。
结合历史会话理解当前问题，但不要把推测当作患者事实。
""".strip()


def checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def prompt_view(row: PromptRelease) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "content": row.content,
        "system_content": row.system_content,
        "checksum": row.checksum,
        "status": row.status,
        "created_at": row.created_at,
        "published_at": row.published_at,
    }


def audit(
    db: AsyncSession,
    *,
    kind: str,
    target_id: str,
    status: str = "completed",
    **detail: object,
) -> None:
    db.add(
        AdminOperation(
            id=str(uuid.uuid4()),
            kind=kind,
            target_id=target_id,
            status=status,
            detail_json=json.dumps(detail, ensure_ascii=False),
        )
    )


async def list_prompts(db: AsyncSession) -> dict[str, Any]:
    rows = list(await db.scalars(select(PromptRelease).order_by(PromptRelease.created_at.desc())))
    published = next((row for row in rows if row.status == "published"), None)
    return {
        "items": [prompt_view(row) for row in rows],
        "effective": prompt_view(published) if published else None,
        "effective_content": published.content if published else DEFAULT_BUSINESS_PROMPT,
        "effective_system_content": published.system_content if published else SAFETY_SYSTEM_PROMPT,
        "using_default": published is None,
        "safety_baseline": SAFETY_SYSTEM_PROMPT,
    }


async def create_prompt(
    db: AsyncSession, *, name: str, content: str, system_content: str = SAFETY_SYSTEM_PROMPT
) -> PromptRelease:
    row = PromptRelease(
        id=str(uuid.uuid4()),
        name=name,
        content=content,
        system_content=system_content,
        checksum=checksum(system_content + "\n\n" + content),
        status="draft",
    )
    db.add(row)
    audit(db, kind="prompt_created", target_id=row.id, checksum=row.checksum)
    await db.commit()
    await db.refresh(row)
    return row


async def require_prompt(db: AsyncSession, prompt_id: str) -> PromptRelease:
    row = await db.get(PromptRelease, prompt_id)
    if row is None:
        raise HTTPException(404, "prompt_release_not_found")
    return row


async def update_prompt(
    db: AsyncSession,
    prompt_id: str,
    *,
    name: str,
    content: str,
    system_content: str = SAFETY_SYSTEM_PROMPT,
) -> PromptRelease:
    row = await require_prompt(db, prompt_id)
    if row.status != "draft":
        raise HTTPException(409, "prompt_release_not_editable")
    row.name = name
    row.content = content
    row.system_content = system_content
    row.checksum = checksum(system_content + "\n\n" + content)
    audit(db, kind="prompt_updated", target_id=row.id, checksum=row.checksum)
    await db.commit()
    await db.refresh(row)
    return row


async def publish_prompt(db: AsyncSession, prompt_id: str) -> PromptRelease:
    row = await require_prompt(db, prompt_id)
    was_archived = row.status == "archived"
    now = datetime.now(UTC)
    await db.execute(
        update(PromptRelease)
        .where(PromptRelease.status == "published", PromptRelease.id != prompt_id)
        .values(status="archived")
    )
    row.status = "published"
    row.published_at = now
    audit(
        db,
        kind="prompt_published",
        target_id=row.id,
        checksum=row.checksum,
        rollback=was_archived,
    )
    await db.commit()
    await db.refresh(row)
    logger.info("system_prompt_published", prompt_release_id=row.id, checksum=row.checksum)
    return row


async def delete_prompt(db: AsyncSession, prompt_id: str) -> None:
    row = await require_prompt(db, prompt_id)
    if row.status != "draft":
        raise HTTPException(409, "prompt_release_not_deletable")
    await db.delete(row)
    audit(db, kind="prompt_deleted", target_id=prompt_id, checksum=row.checksum)
    await db.commit()


async def current_runtime_prompt(db: AsyncSession) -> tuple[str, str | None, str]:
    row = await db.scalar(
        select(PromptRelease)
        .where(PromptRelease.status == "published")
        .order_by(PromptRelease.published_at.desc())
        .limit(1)
    )
    if row is None:
        content = SAFETY_SYSTEM_PROMPT + "\n\n" + DEFAULT_BUSINESS_PROMPT
        return content, None, checksum(content)
    return row.system_content + "\n\n" + row.content, row.id, row.checksum


def assemble_system_prompt(
    business_prompt: str,
    knowledge_context: str = "",
    conversation_memory: str = "",
) -> str:
    sections = [business_prompt.strip()]
    if conversation_memory:
        sections.append(conversation_memory.strip())
    if knowledge_context:
        sections.append(knowledge_context.strip())
    return "\n\n".join(section for section in sections if section)
