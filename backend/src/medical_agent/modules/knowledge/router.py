"""Authenticated knowledge administration endpoints."""

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.api.dependencies import require_admin_token
from medical_agent.core.config import get_settings
from medical_agent.core.database import get_db
from medical_agent.modules.knowledge import admin_service, repository, service
from medical_agent.modules.knowledge.models import KnowledgeOperation
from medical_agent.modules.knowledge.schemas import (
    EmbeddingRebuildRequest,
    EmbeddingRebuildResponse,
    KnowledgeReleaseStatus,
)
from medical_agent.providers.embedding.client import EmbeddingProviderError

router = APIRouter(
    prefix="/admin/knowledge",
    tags=["admin-knowledge"],
    dependencies=[Depends(require_admin_token)],
)
DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/releases/current", response_model=KnowledgeReleaseStatus)
async def current_release(db: DbSession) -> KnowledgeReleaseStatus:
    release = await repository.get_published_release(db)
    if release is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="published_knowledge_release_not_found",
        )
    settings = get_settings()
    vectorized_count = sum(bool(entry.embedding_json) for entry in release.entries)
    return KnowledgeReleaseStatus(
        id=release.id,
        name=release.name,
        source_name=release.source_name,
        status=release.status,
        entry_count=len(release.entries),
        vectorized_count=vectorized_count,
        pending_count=len(release.entries) - vectorized_count,
        embedding_configured=bool(
            settings.embedding_base_url and settings.embedding_api_key and settings.embedding_model
        ),
        embedding_model=settings.embedding_model,
    )


@router.post(
    "/releases/{release_id}/embeddings/rebuild",
    response_model=EmbeddingRebuildResponse,
)
async def rebuild_embeddings(
    release_id: str,
    payload: EmbeddingRebuildRequest,
    db: DbSession,
) -> EmbeddingRebuildResponse:
    settings = get_settings()
    release = await admin_service.require_release(
        db, release_id, draft=payload.rebuild_all, idle=True
    )
    report = json.loads(release.quality_report_json)
    if not payload.rebuild_all and (
        report.get("requires_full_rebuild")
        or (
            report.get("embedding_fingerprint")
            and report["embedding_fingerprint"] != admin_service.fingerprint(settings)
        )
    ):
        raise HTTPException(409, "full_rebuild_required")
    if payload.rebuild_all:
        report["requires_full_rebuild"] = True
    report["embedding_fingerprint"] = admin_service.fingerprint(settings)
    release.quality_report_json = json.dumps(report)
    operation = KnowledgeOperation(
        id=str(uuid.uuid4()),
        release_id=release_id,
        kind="embedding",
        status="running",
        active_slot=release_id,
        detail_json=payload.model_dump_json(),
    )
    db.add(operation)
    await db.commit()
    operation_id = operation.id
    try:
        result = await service.rebuild_release_embeddings(
            db,
            settings=settings,
            release_id=release_id,
            batch_size=payload.batch_size,
            rebuild_all=payload.rebuild_all,
        )
        report["embedding_fingerprint"] = admin_service.fingerprint(settings)
        report["embedding_model"] = settings.embedding_model
        report.pop("requires_full_rebuild", None)
        release.quality_report_json = json.dumps(report)
        operation.status = "completed"
        operation.processed = result.processed_entries
        operation.total = result.processed_entries
        await db.commit()
    except ValueError as exc:
        error_code = str(exc)
        error_status = (
            status.HTTP_404_NOT_FOUND
            if error_code == "knowledge_release_not_found"
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=error_status, detail=error_code) from exc
    except EmbeddingProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    finally:
        await db.rollback()
        stored = await db.get(KnowledgeOperation, operation_id)
        if stored is not None:
            if stored.status == "running":
                stored.status = "failed"
                stored.error = "embedding_rebuild_failed"
            stored.active_slot = None
            stored.finished_at = datetime.now(UTC)
            await db.commit()
    return EmbeddingRebuildResponse(
        release_id=result.release_id,
        total_entries=result.total_entries,
        existing_entries=result.existing_entries,
        processed_entries=result.processed_entries,
        remaining_entries=result.remaining_entries,
        vectorized_count=result.total_entries - result.remaining_entries,
        vector_dimension=result.vector_dimension,
    )
