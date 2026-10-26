"""Protected management APIs. All writes are scoped to explicit releases."""

import hashlib
import io
import json
import uuid
from typing import Annotated, Any, Literal
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from medical_agent.api.dependencies import require_admin_token
from medical_agent.core.config import get_settings
from medical_agent.core.database import get_db, session_factory
from medical_agent.modules.conversation.models import Message
from medical_agent.modules.ingestion.faq_xlsx import parse_faq_xlsx
from medical_agent.modules.knowledge import admin_service as service
from medical_agent.modules.knowledge.admin_schemas import (
    DraftRequest,
    EntryEdit,
    EvaluationCaseGenerateRequest,
    EvaluationRequest,
    SearchRequest,
)
from medical_agent.modules.knowledge.models import (
    KnowledgeEntry,
    KnowledgeOperation,
    KnowledgeRelease,
)
from medical_agent.modules.knowledge.schemas import EmbeddingRebuildRequest

router = APIRouter(
    prefix="/admin/knowledge", tags=["admin-knowledge"], dependencies=[Depends(require_admin_token)]
)
Db = Annotated[AsyncSession, Depends(get_db)]


@router.get("/overview")
async def overview(db: Db) -> dict[str, Any]:
    settings = get_settings()
    return {
        "rag_enabled": settings.rag_enabled,
        "database": db.bind.dialect.name if db.bind else "unknown",
        "embedding_configured": bool(
            settings.embedding_model and settings.embedding_base_url and settings.embedding_api_key
        ),
        "embedding_model": settings.embedding_model,
        "rag_top_k": settings.rag_top_k,
        "rag_min_score": settings.rag_min_score,
        "rag_relative_score": settings.rag_relative_score,
        "chunk_strategy": "一条 FAQ（问题 + 其他问法 + 答案）为一个 chunk",
        "vector_storage": "数据库 JSON 向量，应用层混合检索",
        # One accepted user message is one interaction; the assistant reply is not counted again.
        "interaction_count": await db.scalar(
            select(func.count()).select_from(Message).where(Message.role == "user")
        )
        or 0,
        "active_jobs": await db.scalar(
            select(func.count())
            .select_from(KnowledgeOperation)
            .where(KnowledgeOperation.active_slot.is_not(None))
        )
        or 0,
    }


@router.get("/releases")
async def releases(db: Db) -> list[dict[str, Any]]:
    rows = await db.scalars(select(KnowledgeRelease).order_by(KnowledgeRelease.created_at.desc()))
    return [await service.release_view(db, row) for row in rows]


@router.post("/releases", status_code=201)
async def create_release(payload: DraftRequest, db: Db) -> dict[str, Any]:
    return await service.release_view(
        db, await service.create_draft(db, payload.name, payload.base_release_id)
    )


@router.post("/releases/{release_id}/publish")
async def publish(release_id: str, db: Db) -> dict[str, bool]:
    await service.publish(db, release_id)
    return {"ok": True}


@router.delete("/releases/{release_id}")
async def delete_release(release_id: str, db: Db) -> dict[str, bool]:
    release = await service.require_release(db, release_id, draft=True, idle=True)
    # Explicit deletes also work with legacy SQLite databases without foreign_keys enabled.
    from sqlalchemy import delete

    await db.execute(delete(KnowledgeEntry).where(KnowledgeEntry.release_id == release_id))
    await db.delete(release)
    service.audit(db, release_id, "draft_deleted")
    await db.commit()
    return {"ok": True}


@router.get("/releases/{release_id}/entries")
async def entries(
    release_id: str,
    db: Db,
    q: str = Query(default="", max_length=200),
    vector: Literal["all", "pending", "ready"] = "all",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    await service.require_release(db, release_id)
    conditions = [KnowledgeEntry.release_id == release_id]
    if q.strip():
        conditions.append(
            or_(
                KnowledgeEntry.question.contains(q.strip(), autoescape=True),
                KnowledgeEntry.answer.contains(q.strip(), autoescape=True),
                KnowledgeEntry.category.contains(q.strip(), autoescape=True),
            )
        )
    if vector == "pending":
        conditions.append(KnowledgeEntry.embedding_json.is_(None))
    elif vector == "ready":
        conditions.append(KnowledgeEntry.embedding_json.is_not(None))
    total = await db.scalar(select(func.count()).select_from(KnowledgeEntry).where(*conditions))
    rows = await db.scalars(
        select(KnowledgeEntry)
        .where(*conditions)
        .order_by(KnowledgeEntry.source_sheet, KnowledgeEntry.source_row, KnowledgeEntry.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [service.entry_view(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/releases/{release_id}/entries", status_code=201)
async def add_entry(release_id: str, payload: EntryEdit, db: Db) -> dict[str, Any]:
    return service.entry_view(await service.edit_entry(db, release_id, payload))


@router.put("/releases/{release_id}/entries/{entry_id}")
async def update_entry(
    release_id: str, entry_id: str, payload: EntryEdit, db: Db
) -> dict[str, Any]:
    return service.entry_view(await service.edit_entry(db, release_id, payload, entry_id))


@router.delete("/releases/{release_id}/entries/{entry_id}")
async def delete_entry(release_id: str, entry_id: str, db: Db) -> dict[str, bool]:
    release = await service.require_release(db, release_id, draft=True, idle=True)
    entry = await db.get(KnowledgeEntry, entry_id)
    if entry is None or entry.release_id != release_id:
        raise HTTPException(404, "knowledge_entry_not_found")
    await db.delete(entry)
    await db.flush()
    release.entry_count = (
        await db.scalar(
            select(func.count())
            .select_from(KnowledgeEntry)
            .where(KnowledgeEntry.release_id == release_id)
        )
        or 0
    )
    service.audit(db, release_id, "entry_deleted", entry_id=entry_id)
    await db.commit()
    return {"ok": True}


@router.post("/releases/{release_id}/import")
async def import_xlsx(
    release_id: str, db: Db, file: Annotated[UploadFile, File()]
) -> dict[str, Any]:
    await service.require_release(db, release_id, draft=True, idle=True)
    source_name = (file.filename or "FAQ.xlsx").replace("\\", "/").split("/")[-1]
    if not source_name.lower().endswith(".xlsx"):
        raise HTTPException(422, "xlsx_required")
    data = await file.read(10 * 1024 * 1024 + 1)
    await file.close()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "xlsx_too_large")
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > 50 * 1024 * 1024:
                raise HTTPException(413, "xlsx_too_large")
        parsed, profile = await run_in_threadpool(parse_faq_xlsx, io.BytesIO(data))
    except (BadZipFile, ValueError, KeyError, IndexError, ParseError) as exc:
        raise HTTPException(422, "invalid_faq_xlsx") from exc
    if not parsed or profile.valid_rows != profile.source_rows:
        raise HTTPException(422, "faq_has_invalid_rows")
    counts = await service.merge_entries(
        db, release_id, parsed, source_name, hashlib.sha256(data).hexdigest()
    )
    return {**counts, "warnings": profile.warnings}


@router.post("/releases/{release_id}/jobs", status_code=202)
async def create_job(
    release_id: str, payload: EmbeddingRebuildRequest, db: Db, background: BackgroundTasks
) -> dict[str, Any]:
    release = await service.require_release(db, release_id, draft=payload.rebuild_all, idle=True)
    settings = get_settings()
    if not all((settings.embedding_model, settings.embedding_base_url, settings.embedding_api_key)):
        raise HTTPException(409, "embedding_not_configured")
    report = json.loads(release.quality_report_json)
    saved_fingerprint = report.get("embedding_fingerprint")
    if (
        saved_fingerprint
        and saved_fingerprint != service.fingerprint(settings)
        and not payload.rebuild_all
    ):
        raise HTTPException(409, "embedding_model_changed_rebuild_draft")
    # Mark the target fingerprint up front so a failed full rebuild cannot be resumed
    # with another model via incremental mode.
    if payload.rebuild_all:
        report["embedding_fingerprint"] = service.fingerprint(settings)
        report["requires_full_rebuild"] = True
        release.quality_report_json = json.dumps(report)
    elif report.get("requires_full_rebuild"):
        raise HTTPException(409, "full_rebuild_required")
    report["embedding_fingerprint"] = service.fingerprint(settings)
    release.quality_report_json = json.dumps(report)
    counts = await service.release_view(db, release)
    job = KnowledgeOperation(
        id=str(uuid.uuid4()),
        release_id=release_id,
        kind="embedding",
        total=counts["entry_count"] if payload.rebuild_all else counts["pending_count"],
        status="queued",
        active_slot=release_id,
        detail_json=payload.model_dump_json(),
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "knowledge_job_active") from exc
    background.add_task(service.run_embedding_job, job.id, session_factory, settings)
    return service.operation_view(job)


@router.get("/operations")
async def operations(
    db: Db, page: int = Query(default=1, ge=1), kind: str | None = None
) -> dict[str, Any]:
    conditions = [KnowledgeOperation.kind == kind] if kind else []
    rows = await db.scalars(
        select(KnowledgeOperation)
        .where(*conditions)
        .order_by(KnowledgeOperation.created_at.desc())
        .offset((page - 1) * 30)
        .limit(30)
    )
    total = await db.scalar(select(func.count()).select_from(KnowledgeOperation).where(*conditions))
    return {
        "items": [service.operation_view(row, include_detail=False) for row in rows],
        "total": total,
    }


@router.get("/operations/{operation_id}")
async def operation(operation_id: str, db: Db) -> dict[str, Any]:
    row = await db.get(KnowledgeOperation, operation_id)
    if row is None:
        raise HTTPException(404, "operation_not_found")
    return service.operation_view(row)


@router.post("/search")
async def search(payload: SearchRequest, db: Db) -> dict[str, Any]:
    return await service.search(db, payload, get_settings())


@router.post("/evaluation-cases/generate")
async def generate_evaluation_cases(
    payload: EvaluationCaseGenerateRequest, db: Db
) -> dict[str, Any]:
    return await service.generate_evaluation_cases(
        db,
        release_id=payload.release_id,
        limit=payload.limit,
        source=payload.source,
    )


@router.post("/evaluations")
async def evaluate(payload: EvaluationRequest, db: Db) -> dict[str, Any]:
    return service.operation_view(await service.evaluate(db, payload, get_settings()))
