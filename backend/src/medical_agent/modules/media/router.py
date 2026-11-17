"""Private image upload, retrieval, and pending-media deletion endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.api.dependencies import current_owner_id
from medical_agent.core.config import get_settings
from medical_agent.core.database import get_db
from medical_agent.modules.media import service
from medical_agent.modules.media.schemas import MediaRead

router = APIRouter(prefix="/media", tags=["media"])
DbSession = Annotated[AsyncSession, Depends(get_db)]
OwnerId = Annotated[str, Depends(current_owner_id)]


@router.post("/images", response_model=list[MediaRead], status_code=status.HTTP_201_CREATED)
async def upload_images(
    images: Annotated[list[UploadFile], File()],
    db: DbSession,
    owner_id: OwnerId,
) -> list[MediaRead]:
    items = await service.store_images(
        db,
        uploads=images,
        owner_id=owner_id,
        settings=get_settings(),
    )
    await db.commit()
    return [MediaRead.model_validate(item) for item in items]


@router.get("/{media_id}/content", response_class=FileResponse)
async def get_media_content(media_id: str, db: DbSession, owner_id: OwnerId) -> FileResponse:
    settings = get_settings()
    media = await service.get_owned_media(db, media_id=media_id, owner_id=owner_id)
    path = service.storage_path(media, settings)
    if not path.is_file():
        from fastapi import HTTPException

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="media_file_not_found")
    return FileResponse(
        path,
        media_type=media.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media(media_id: str, db: DbSession, owner_id: OwnerId) -> Response:
    settings = get_settings()
    media = await service.get_owned_media(db, media_id=media_id, owner_id=owner_id)
    await service.delete_pending_media(db, media=media, settings=settings)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
