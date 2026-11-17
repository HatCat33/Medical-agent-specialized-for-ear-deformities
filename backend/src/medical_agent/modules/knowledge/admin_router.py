"""Protected management APIs. All writes are scoped to explicit releases."""

import hashlib
import io
import json
import re
import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from medical_agent.api.dependencies import require_admin_token
from medical_agent.core.config import get_settings
from medical_agent.core.database import get_db, session_factory
from medical_agent.core.redis import store
from medical_agent.modules.ingestion.faq_xlsx import parse_faq_xlsx
from medical_agent.modules.knowledge import admin_service as service
from medical_agent.modules.knowledge import (
    conversation_test_service,
    interaction_analytics,
    interaction_export,
    retriever,
)
from medical_agent.modules.knowledge.admin_schemas import (
    ConversationTestRequest,
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
from medical_agent.modules.knowledge.service import question_key
from medical_agent.modules.system import counter_service

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
        "redis_enabled": settings.redis_enabled,
        "redis_connected": store.available,
        "embedding_cache_enabled": (
            settings.redis_enabled and settings.embedding_cache_ttl_seconds > 0
        ),
        "retrieval_cache_enabled": (
            settings.redis_enabled and settings.retrieval_cache_ttl_seconds > 0
        ),
        "tts_cache_enabled": settings.redis_enabled and settings.tts_cache_ttl_seconds > 0,
        "embedding_configured": bool(
            settings.embedding_model and settings.embedding_base_url and settings.embedding_api_key
        ),
        "embedding_model": settings.embedding_model,
        "rag_top_k": settings.rag_top_k,
        "rag_min_score": settings.rag_min_score,
        "rag_relative_score": settings.rag_relative_score,
        "reranker_enabled": settings.reranker_enabled,
        "reranker_configured": bool(
            settings.reranker_base_url
            and settings.reranker_api_key
            and settings.reranker_model
        ),
        "reranker_model": settings.reranker_model,
        "knowledge_draft_limit": settings.knowledge_draft_limit,
        "knowledge_draft_count": await db.scalar(
            select(func.count())
            .select_from(KnowledgeRelease)
            .where(KnowledgeRelease.status == "draft")
        )
        or 0,
        "chunk_strategy": "一条 FAQ（问题 + 其他问法 + 答案）为一个 chunk",
        "vector_storage": "数据库 JSON 向量，应用层混合检索",
        "interaction_count": await counter_service.value(
            db, counter_service.PATIENT_INTERACTIONS
        ),
        "active_jobs": await db.scalar(
            select(func.count())
            .select_from(KnowledgeOperation)
            .where(KnowledgeOperation.active_slot.is_not(None))
        )
        or 0,
    }


@router.get("/interaction-analytics")
async def interaction_analytics_view(
    db: Db,
    start_date: date | None = None,
    end_date: date | None = None,
    user_id: str = Query(default="", max_length=40),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return await interaction_analytics.analytics(
        db,
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
        page=page,
        page_size=page_size,
    )


@router.get("/interaction-analytics/export")
async def export_interaction_records(
    db: Db,
    start_date: date | None = None,
    end_date: date | None = None,
    user_id: str = Query(default="", max_length=40),
) -> Response:
    items = await interaction_analytics.export_items(
        db,
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
    )
    if not items:
        raise HTTPException(404, "interaction_export_empty")
    generated_at = datetime.now(interaction_analytics.DISPLAY_TIMEZONE)
    filename = f"用户交互记录-{generated_at:%Y%m%d-%H%M%S}.xlsx"
    content = interaction_export.build_interaction_workbook(
        items,
        sheet_name=generated_at.strftime("%Y.%m"),
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f"attachment; filename=interaction-records-{generated_at:%Y%m%d-%H%M%S}.xlsx; "
                f"filename*=UTF-8''{quote(filename)}"
            ),
            "Cache-Control": "no-store",
        },
    )


@router.get("/releases")
async def releases(db: Db) -> list[dict[str, Any]]:
    rows = await db.scalars(select(KnowledgeRelease).order_by(KnowledgeRelease.created_at.desc()))
    return [await service.release_view(db, row) for row in rows]


@router.post("/releases", status_code=201)
async def create_release(payload: DraftRequest, db: Db) -> dict[str, Any]:
    return await service.release_view(
        db,
        await service.create_draft(
            db,
            payload.name,
            payload.base_release_id,
            draft_limit=get_settings().knowledge_draft_limit,
        ),
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
    category: str = Query(default="", max_length=120),
    search_mode: Literal["text", "semantic"] = "text",
    vector: Literal["all", "pending", "ready"] = "all",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    await service.require_release(db, release_id)
    conditions = [KnowledgeEntry.release_id == release_id]
    query = q.strip()
    semantic_order: dict[str, int] = {}
    if query and search_mode == "semantic":
        settings = get_settings().model_copy(
            update={
                "rag_top_k": 100,
                "rag_min_score": 0.05,
                "rag_relative_score": 0.2,
                "reranker_enabled": False,
            }
        )
        result = await retriever.retrieve_detailed(
            db, query=query, settings=settings, release_id=release_id
        )
        semantic_order = {hit.entry_id: index for index, hit in enumerate(result.hits)}
        if not semantic_order:
            return {
                "items": [], "total": 0, "page": page, "page_size": page_size,
                "categories": list(
                    await db.scalars(
                        select(KnowledgeEntry.category)
                        .where(KnowledgeEntry.release_id == release_id)
                        .distinct()
                        .order_by(KnowledgeEntry.category)
                    )
                ),
            }
        conditions.append(KnowledgeEntry.id.in_(semantic_order))
    tokens = [item for item in re.split(r"[\s,，。；;、]+", query) if item]
    searchable = (
        KnowledgeEntry.question,
        KnowledgeEntry.aliases,
        KnowledgeEntry.answer,
        KnowledgeEntry.category,
    )
    for token in tokens if search_mode == "text" else []:
        conditions.append(
            or_(*(column.contains(token, autoescape=True) for column in searchable))
        )
    if category.strip():
        conditions.append(KnowledgeEntry.category == category.strip())
    if vector == "pending":
        conditions.append(KnowledgeEntry.embedding_json.is_(None))
    elif vector == "ready":
        conditions.append(KnowledgeEntry.embedding_json.is_not(None))
    total = await db.scalar(select(func.count()).select_from(KnowledgeEntry).where(*conditions))
    relevance = (
        case((KnowledgeEntry.question == query, 0), else_=1),
        case((KnowledgeEntry.question.contains(query, autoescape=True), 0), else_=1),
        case((KnowledgeEntry.aliases.contains(query, autoescape=True), 0), else_=1),
        case((KnowledgeEntry.category.contains(query, autoescape=True), 0), else_=1),
        case((KnowledgeEntry.answer.contains(query, autoescape=True), 0), else_=1),
    ) if query and search_mode == "text" else ()
    semantic_relevance = (
        case(semantic_order, value=KnowledgeEntry.id, else_=len(semantic_order)),
    ) if semantic_order else ()
    rows = await db.scalars(
        select(KnowledgeEntry)
        .where(*conditions)
        .order_by(
            *relevance,
            *semantic_relevance,
            KnowledgeEntry.source_sheet,
            KnowledgeEntry.source_row,
            KnowledgeEntry.id,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    categories = list(
        await db.scalars(
            select(KnowledgeEntry.category)
            .where(KnowledgeEntry.release_id == release_id)
            .distinct()
            .order_by(KnowledgeEntry.category)
        )
    )
    return {
        "items": [service.entry_view(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "categories": categories,
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


async def _read_xlsx(file: UploadFile) -> tuple[str, bytes, list[Any], Any]:
    source_name = (file.filename or "knowledge.xlsx").replace("\\", "/").split("/")[-1]
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
    return source_name, data, parsed, profile


@router.post("/releases/{release_id}/import-preview")
async def preview_xlsx(
    release_id: str, db: Db, file: Annotated[UploadFile, File()]
) -> dict[str, Any]:
    source_name, data, parsed, profile = await _read_xlsx(file)
    result = await service.preview_entries(db, release_id, parsed)
    return {
        **result,
        "source_name": source_name,
        "checksum": hashlib.sha256(data).hexdigest(),
        "warnings": profile.warnings,
    }


@router.post("/releases/{release_id}/import-apply")
async def apply_xlsx(
    release_id: str,
    db: Db,
    file: Annotated[UploadFile, File()],
    selected_rows: Annotated[str, Form()],
    checksum: Annotated[str, Form(min_length=64, max_length=64)],
    draft_checksum: Annotated[str, Form(min_length=64, max_length=64)],
) -> dict[str, Any]:
    source_name, data, parsed, profile = await _read_xlsx(file)
    actual_checksum = hashlib.sha256(data).hexdigest()
    if actual_checksum != checksum:
        raise HTTPException(409, "xlsx_changed_after_preview")
    try:
        requested = {int(row) for row in json.loads(selected_rows)}
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(422, "invalid_import_selection") from exc
    selected = [item for item in parsed if item.source_row in requested]
    if not selected or len(selected) != len(requested):
        raise HTTPException(422, "invalid_import_selection")
    selected_question_keys = [question_key(item.question) for item in selected]
    if len(selected_question_keys) != len(set(selected_question_keys)):
        raise HTTPException(422, "invalid_import_selection")
    current = await service.preview_entries(db, release_id, parsed)
    if current["draft_checksum"] != draft_checksum:
        raise HTTPException(409, "knowledge_draft_changed_after_preview")
    counts = await service.merge_entries(
        db, release_id, selected, source_name, actual_checksum
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


@router.post("/conversation-tests")
async def conversation_test(payload: ConversationTestRequest, db: Db) -> dict[str, Any]:
    return await conversation_test_service.run(db, payload)
