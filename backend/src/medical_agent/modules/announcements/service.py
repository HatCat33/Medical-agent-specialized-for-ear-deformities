"""Announcement draft and publication operations."""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.modules.announcements.models import Announcement
from medical_agent.modules.system.prompt_service import audit


def announcement_view(row: Announcement) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "content": row.content,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "published_at": row.published_at,
    }


async def list_admin(db: AsyncSession) -> list[dict[str, Any]]:
    rows = await db.scalars(
        select(Announcement).order_by(Announcement.created_at.desc())
    )
    return [announcement_view(row) for row in rows]


async def list_published(db: AsyncSession) -> list[dict[str, Any]]:
    rows = await db.scalars(
        select(Announcement)
        .where(Announcement.status == "published")
        .order_by(Announcement.published_at.desc())
    )
    return [announcement_view(row) for row in rows]


async def require_announcement(db: AsyncSession, announcement_id: str) -> Announcement:
    row = await db.get(Announcement, announcement_id)
    if row is None:
        raise HTTPException(404, "announcement_not_found")
    return row


async def create_draft(
    db: AsyncSession, *, title: str, content: str, draft_limit: int
) -> Announcement:
    draft_count = await db.scalar(
        select(func.count())
        .select_from(Announcement)
        .where(Announcement.status == "draft")
    )
    if (draft_count or 0) >= draft_limit:
        raise HTTPException(409, "announcement_draft_limit_reached")
    row = Announcement(
        id=str(uuid.uuid4()), title=title, content=content, status="draft"
    )
    db.add(row)
    audit(db, kind="announcement_created", target_id=row.id)
    await db.commit()
    await db.refresh(row)
    return row


async def inventory(db: AsyncSession, *, draft_limit: int) -> dict[str, Any]:
    items = await list_admin(db)
    return {
        "items": items,
        "draft_count": sum(item["status"] == "draft" for item in items),
        "draft_limit": draft_limit,
    }


async def update_draft(
    db: AsyncSession, announcement_id: str, *, title: str, content: str
) -> Announcement:
    row = await require_announcement(db, announcement_id)
    if row.status != "draft":
        raise HTTPException(409, "announcement_not_editable")
    row.title = title
    row.content = content
    row.updated_at = datetime.now(UTC)
    audit(db, kind="announcement_updated", target_id=row.id)
    await db.commit()
    await db.refresh(row)
    return row


async def publish(db: AsyncSession, announcement_id: str) -> Announcement:
    row = await require_announcement(db, announcement_id)
    if row.status != "draft":
        raise HTTPException(409, "announcement_not_publishable")
    now = datetime.now(UTC)
    row.status = "published"
    row.updated_at = now
    row.published_at = now
    audit(db, kind="announcement_published", target_id=row.id)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_draft(db: AsyncSession, announcement_id: str) -> None:
    row = await require_announcement(db, announcement_id)
    if row.status != "draft":
        raise HTTPException(409, "announcement_not_deletable")
    await db.delete(row)
    audit(db, kind="announcement_deleted", target_id=announcement_id)
    await db.commit()
