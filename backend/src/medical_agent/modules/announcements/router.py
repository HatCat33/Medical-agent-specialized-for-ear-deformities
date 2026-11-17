"""Public and protected announcement endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.api.dependencies import require_admin_token
from medical_agent.core.config import get_settings
from medical_agent.core.database import get_db
from medical_agent.modules.announcements import service
from medical_agent.modules.announcements.schemas import AnnouncementWrite

Db = Annotated[AsyncSession, Depends(get_db)]
public_router = APIRouter(prefix="/announcements", tags=["announcements"])
admin_router = APIRouter(
    prefix="/admin/announcements",
    tags=["admin-announcements"],
    dependencies=[Depends(require_admin_token)],
)


@public_router.get("")
async def published_announcements(db: Db) -> list[dict[str, Any]]:
    return await service.list_published(db)


@admin_router.get("")
async def announcements(db: Db) -> dict[str, Any]:
    return await service.inventory(
        db, draft_limit=get_settings().announcement_draft_limit
    )


@admin_router.post("", status_code=status.HTTP_201_CREATED)
async def create_announcement(payload: AnnouncementWrite, db: Db) -> dict[str, Any]:
    return service.announcement_view(
        await service.create_draft(
            db,
            **payload.model_dump(),
            draft_limit=get_settings().announcement_draft_limit,
        )
    )


@admin_router.put("/{announcement_id}")
async def update_announcement(
    announcement_id: str, payload: AnnouncementWrite, db: Db
) -> dict[str, Any]:
    return service.announcement_view(
        await service.update_draft(db, announcement_id, **payload.model_dump())
    )


@admin_router.post("/{announcement_id}/publish")
async def publish_announcement(announcement_id: str, db: Db) -> dict[str, Any]:
    return service.announcement_view(await service.publish(db, announcement_id))


@admin_router.delete("/{announcement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_announcement(announcement_id: str, db: Db) -> Response:
    await service.delete_draft(db, announcement_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
