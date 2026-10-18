"""Validated private media storage and message attachment lifecycle."""

import asyncio
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.core.config import Settings
from medical_agent.core.logging import get_logger
from medical_agent.modules.conversation.models import Message
from medical_agent.modules.media.models import MediaObject
from medical_agent.providers.llm.protocol import ImageInput

logger = get_logger(__name__)

_IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _detect_image_media_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _safe_original_name(filename: str | None, media_type: str) -> str:
    name = Path(filename or "image").name.replace("\x00", "").strip()
    return (name or f"image{_IMAGE_EXTENSIONS[media_type]}")[:255]


def storage_path(media: MediaObject, settings: Settings) -> Path:
    root = Path(settings.media_storage_path).resolve()
    candidate = (root / media.storage_key).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="media_storage_key_invalid",
        )
    return candidate


async def store_images(
    db: AsyncSession,
    *,
    uploads: list[UploadFile],
    owner_id: str,
    settings: Settings,
) -> list[MediaObject]:
    if not uploads or len(uploads) > settings.media_max_image_count:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="image_count_invalid",
        )

    prepared: list[tuple[UploadFile, bytes, str]] = []
    total_bytes = 0
    for upload in uploads:
        try:
            data = await upload.read(settings.media_max_image_bytes + 1)
        finally:
            await upload.close()
        if not data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="image_empty")
        if len(data) > settings.media_max_image_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="image_too_large",
            )
        detected_type = _detect_image_media_type(data)
        if detected_type is None:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="image_type_not_supported",
            )
        total_bytes += len(data)
        if total_bytes > settings.media_max_total_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="images_total_too_large",
            )
        prepared.append((upload, data, detected_type))

    stored_paths: list[Path] = []
    media_objects: list[MediaObject] = []
    try:
        for upload, data, media_type in prepared:
            media_id = str(uuid.uuid4())
            extension = _IMAGE_EXTENSIONS[media_type]
            storage_key = f"images/{media_id[:2]}/{media_id}{extension}"
            media = MediaObject(
                id=media_id,
                owner_id=owner_id,
                kind="image",
                original_name=_safe_original_name(upload.filename, media_type),
                media_type=media_type,
                size_bytes=len(data),
                storage_key=storage_key,
                status="pending",
            )
            path = storage_path(media, settings)
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_bytes, data)
            stored_paths.append(path)
            media_objects.append(media)
        db.add_all(media_objects)
        await db.flush()
    except Exception:
        for path in stored_paths:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        raise

    logger.info(
        "media_images_stored",
        count=len(media_objects),
        total_bytes=total_bytes,
        media_ids=[item.id for item in media_objects],
    )
    return media_objects


async def get_owned_media(
    db: AsyncSession,
    *,
    media_id: str,
    owner_id: str,
) -> MediaObject:
    media = await db.scalar(
        select(MediaObject).where(
            MediaObject.id == media_id,
            MediaObject.owner_id == owner_id,
        )
    )
    if media is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="media_not_found")
    return media


async def claim_media(
    db: AsyncSession,
    *,
    media_ids: list[str],
    owner_id: str,
    conversation_id: str,
    message: Message,
    settings: Settings,
) -> list[MediaObject]:
    if not media_ids:
        return []
    if len(media_ids) > settings.media_max_image_count or len(set(media_ids)) != len(media_ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="attachment_ids_invalid")

    found = list(
        await db.scalars(
            select(MediaObject).where(
                MediaObject.id.in_(media_ids),
                MediaObject.owner_id == owner_id,
            )
        )
    )
    by_id = {item.id: item for item in found}
    if any(media_id not in by_id for media_id in media_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="attachment_not_found")

    ordered = [by_id[media_id] for media_id in media_ids]
    if any(item.status != "pending" or item.kind != "image" for item in ordered):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="attachment_not_available")

    for item in ordered:
        item.conversation_id = conversation_id
        item.message = message
        item.status = "attached"
    await db.flush()
    logger.info(
        "media_attached_to_message",
        conversation_id=conversation_id,
        message_id=message.id,
        media_ids=media_ids,
    )
    return ordered


async def load_image_inputs(
    attachments: list[MediaObject],
    *,
    settings: Settings,
) -> tuple[ImageInput, ...]:
    images: list[ImageInput] = []
    for media in attachments:
        if media.kind != "image":
            continue
        path = storage_path(media, settings)
        if not path.is_file():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="media_file_missing",
            )
        data = await asyncio.to_thread(path.read_bytes)
        images.append(ImageInput(media_type=media.media_type, data=data))
    return tuple(images)


async def delete_pending_media(
    db: AsyncSession,
    *,
    media: MediaObject,
    settings: Settings,
) -> None:
    if media.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="attached_media_not_deletable")
    await asyncio.to_thread(storage_path(media, settings).unlink, missing_ok=True)
    await db.delete(media)
    logger.info("pending_media_deleted", media_id=media.id)


async def delete_conversation_media(
    db: AsyncSession,
    *,
    conversation_id: str,
    owner_id: str,
    settings: Settings,
) -> None:
    items = list(
        await db.scalars(
            select(MediaObject).where(
                MediaObject.conversation_id == conversation_id,
                MediaObject.owner_id == owner_id,
            )
        )
    )
    for media in items:
        await asyncio.to_thread(storage_path(media, settings).unlink, missing_ok=True)
    await db.execute(
        delete(MediaObject).where(
            MediaObject.conversation_id == conversation_id,
            MediaObject.owner_id == owner_id,
        )
    )
    if items:
        logger.info(
            "conversation_media_deleted",
            conversation_id=conversation_id,
            count=len(items),
        )
