"""Versioned knowledge persistence operations."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from medical_agent.modules.knowledge.models import KnowledgeRelease


async def get_release_by_checksum(
    db: AsyncSession,
    *,
    checksum: str,
) -> KnowledgeRelease | None:
    return await db.scalar(
        select(KnowledgeRelease).where(KnowledgeRelease.source_checksum == checksum)
    )


async def get_published_release(db: AsyncSession) -> KnowledgeRelease | None:
    return await db.scalar(
        select(KnowledgeRelease)
        .where(KnowledgeRelease.status == "published")
        .order_by(KnowledgeRelease.published_at.desc())
        .options(selectinload(KnowledgeRelease.entries))
        .limit(1)
    )


async def get_release_by_id(
    db: AsyncSession,
    *,
    release_id: str,
) -> KnowledgeRelease | None:
    return await db.scalar(
        select(KnowledgeRelease)
        .where(KnowledgeRelease.id == release_id)
        .options(selectinload(KnowledgeRelease.entries))
    )


async def archive_published_releases(db: AsyncSession) -> None:
    await db.execute(
        update(KnowledgeRelease)
        .where(KnowledgeRelease.status == "published")
        .values(status="archived")
    )

