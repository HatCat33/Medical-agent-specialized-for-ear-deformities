"""Draft editing, durable embedding jobs, audit history and measured retrieval."""

import hashlib
import json
import re
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from medical_agent.core.config import Settings
from medical_agent.core.logging import get_logger
from medical_agent.modules.knowledge import repository, retriever, service
from medical_agent.modules.knowledge.admin_schemas import (
    EntryEdit,
    EvaluationRequest,
    SearchRequest,
)
from medical_agent.modules.knowledge.models import (
    KnowledgeEntry,
    KnowledgeOperation,
    KnowledgeRelease,
)
from medical_agent.modules.knowledge.schemas import FaqEntryInput

logger = get_logger(__name__)


def audit(db: AsyncSession, release_id: str, kind: str, **detail: object) -> KnowledgeOperation:
    event = KnowledgeOperation(
        id=str(uuid.uuid4()),
        release_id=release_id,
        kind=kind,
        status="completed",
        detail_json=json.dumps(detail, ensure_ascii=False),
        finished_at=datetime.now(UTC),
    )
    db.add(event)
    logger.info("knowledge_admin_operation", release_id=release_id, operation=kind)
    return event


async def require_release(
    db: AsyncSession, release_id: str, *, draft: bool = False, idle: bool = False
) -> KnowledgeRelease:
    # Serialize same-release writes on PostgreSQL; jobs also have a unique active slot.
    if draft or idle:
        await db.execute(
            update(KnowledgeRelease)
            .where(KnowledgeRelease.id == release_id)
            .values(entry_count=KnowledgeRelease.entry_count)
        )
    release = await db.scalar(
        select(KnowledgeRelease)
        .where(KnowledgeRelease.id == release_id)
        .execution_options(populate_existing=True)
    )
    if release is None:
        raise HTTPException(404, "knowledge_release_not_found")
    if draft and release.status != "draft":
        raise HTTPException(409, "create_draft_before_editing")
    if idle and await db.scalar(
        select(KnowledgeOperation.id).where(KnowledgeOperation.active_slot == release_id)
    ):
        raise HTTPException(409, "knowledge_job_active")
    return release


def fingerprint(settings: Settings) -> str:
    return hashlib.sha256(
        f"{settings.embedding_base_url.rstrip('/')}|{settings.embedding_model}".encode()
    ).hexdigest()


async def release_view(db: AsyncSession, release: KnowledgeRelease) -> dict[str, Any]:
    total = (
        await db.scalar(
            select(func.count())
            .select_from(KnowledgeEntry)
            .where(KnowledgeEntry.release_id == release.id)
        )
        or 0
    )
    embedded = (
        await db.scalar(
            select(func.count())
            .select_from(KnowledgeEntry)
            .where(
                KnowledgeEntry.release_id == release.id,
                KnowledgeEntry.embedding_json.is_not(None),
                KnowledgeEntry.embedding_json != "",
            )
        )
        or 0
    )
    return {
        "id": release.id,
        "name": release.name,
        "source_name": release.source_name,
        "status": release.status,
        "entry_count": total,
        "vectorized_count": embedded,
        "pending_count": total - embedded,
        "created_at": release.created_at,
        "published_at": release.published_at,
        "quality_report": json.loads(release.quality_report_json),
    }


def entry_view(entry: KnowledgeEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "release_id": entry.release_id,
        "category": entry.category,
        "question": entry.question,
        "answer": entry.answer,
        "aliases": entry.aliases,
        "effective_date": entry.effective_date,
        "source_sheet": entry.source_sheet,
        "source_row": entry.source_row,
        "vectorized": bool(entry.embedding_json),
        "chunk_text": service.embedding_text(entry),
    }


async def create_draft(db: AsyncSession, name: str, base_id: str | None) -> KnowledgeRelease:
    if base_id:
        await require_release(db, base_id, idle=True)
    base = await repository.get_release_by_id(db, release_id=base_id) if base_id else None
    if base_id and base is None:
        raise HTTPException(404, "knowledge_release_not_found")
    draft = KnowledgeRelease(
        id=str(uuid.uuid4()),
        name=name,
        source_name=base.source_name if base else "管理端",
        source_checksum=uuid.uuid4().hex + uuid.uuid4().hex,
        status="draft",
        entry_count=len(base.entries) if base else 0,
        quality_report_json=base.quality_report_json if base else "{}",
    )
    db.add(draft)
    await db.flush()
    if base:
        for old in base.entries:
            db.add(
                KnowledgeEntry(
                    id=str(uuid.uuid4()),
                    release_id=draft.id,
                    category=old.category,
                    question=old.question,
                    aliases=old.aliases,
                    answer=old.answer,
                    effective_date=old.effective_date,
                    source_sheet=old.source_sheet,
                    source_row=old.source_row,
                    search_text=old.search_text,
                    embedding_json=old.embedding_json,
                )
            )
    audit(db, draft.id, "draft_created", base_release_id=base_id)
    await db.commit()
    return draft


async def edit_entry(
    db: AsyncSession, release_id: str, payload: EntryEdit, entry_id: str | None = None
) -> KnowledgeEntry:
    release = await require_release(db, release_id, draft=True, idle=True)
    entry = await db.get(KnowledgeEntry, entry_id) if entry_id else None
    if entry_id and (entry is None or entry.release_id != release_id):
        raise HTTPException(404, "knowledge_entry_not_found")
    entries = list(
        await db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.release_id == release_id))
    )
    if any(
        e.id != entry_id
        and service.question_key(e.question) == service.question_key(payload.question)
        for e in entries
    ):
        raise HTTPException(409, "duplicate_question")
    if entry is None:
        row = max((e.source_row for e in entries if e.source_sheet == "管理端"), default=0) + 1
        entry = KnowledgeEntry(
            id=str(uuid.uuid4()), release_id=release_id, source_sheet="管理端", source_row=row
        )
        db.add(entry)
        release.entry_count = len(entries) + 1
    old_text = service.embedding_text(entry)
    for key, value in payload.model_dump().items():
        setattr(entry, key, value)
    if old_text != service.embedding_text(entry):
        entry.embedding_json = None
    entry.search_text = f"{entry.question} {entry.aliases} {entry.answer}"
    audit(db, release_id, "entry_updated" if entry_id else "entry_created", entry_id=entry.id)
    await db.commit()
    return entry


async def merge_entries(
    db: AsyncSession,
    release_id: str,
    incoming: list[FaqEntryInput],
    source_name: str,
    checksum: str,
) -> dict[str, int]:
    release = await require_release(db, release_id, draft=True, idle=True)
    accepted, conflicts = service.deduplicate_faq_entries(incoming)
    if conflicts:
        raise HTTPException(422, "faq_conflicting_duplicate_answers")
    existing = list(
        await db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.release_id == release_id))
    )
    by_question = {service.question_key(e.question): e for e in existing}
    counts = {"added": 0, "updated": 0, "unchanged": 0}
    # A distinct source namespace prevents row collisions with previous imports.
    source_sheet = f"{source_name[:60]}:{checksum[:10]}"
    for item in accepted:
        entry = by_question.get(service.question_key(item.question))
        if entry is None:
            entry = KnowledgeEntry(
                id=str(uuid.uuid4()),
                release_id=release_id,
                source_sheet=source_sheet,
                source_row=item.source_row,
            )
            db.add(entry)
            counts["added"] += 1
        elif all(
            getattr(entry, k) == getattr(item, k)
            for k in ("question", "aliases", "answer", "category", "effective_date")
        ):
            counts["unchanged"] += 1
            continue
        else:
            counts["updated"] += 1
            entry.source_sheet = source_sheet
            entry.source_row = item.source_row
        old_text = service.embedding_text(entry)
        for key in ("question", "aliases", "answer", "category", "effective_date"):
            setattr(entry, key, getattr(item, key))
        if old_text != service.embedding_text(entry):
            entry.embedding_json = None
        entry.search_text = f"{entry.question} {entry.aliases} {entry.answer}"
    release.entry_count = len(existing) + counts["added"]
    release.source_name = source_name[:255]
    audit(db, release_id, "xlsx_merged", source_name=source_name, checksum=checksum, **counts)
    await db.commit()
    return counts


async def publish(db: AsyncSession, release_id: str) -> None:
    # Consistent lock ordering across all publish/rollback requests.
    await db.execute(select(KnowledgeRelease.id).order_by(KnowledgeRelease.id).with_for_update())
    release = await require_release(db, release_id, idle=True)
    if json.loads(release.quality_report_json).get("requires_full_rebuild"):
        raise HTTPException(409, "full_rebuild_required")
    count = await db.scalar(
        select(func.count())
        .select_from(KnowledgeEntry)
        .where(KnowledgeEntry.release_id == release_id)
    )
    if not count:
        raise HTTPException(409, "cannot_publish_empty_release")
    # Lock all releases in a stable order to serialize publication/rollback.
    await repository.archive_published_releases(db)
    release.status = "published"
    release.published_at = datetime.now(UTC)
    audit(db, release_id, "release_published")
    await db.commit()


def operation_view(op: KnowledgeOperation, *, include_detail: bool = True) -> dict[str, Any]:
    return {
        "id": op.id,
        "release_id": op.release_id,
        "kind": op.kind,
        "status": op.status,
        "processed": op.processed,
        "total": op.total,
        "error": op.error,
        "detail": json.loads(op.detail_json) if include_detail else {},
        "created_at": op.created_at,
        "finished_at": op.finished_at,
    }


async def run_embedding_job(
    job_id: str, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    async with factory() as db:
        job = await db.get(KnowledgeOperation, job_id)
        if job is None:
            return
        options = json.loads(job.detail_json)
        job.status = "running"
        await db.commit()

        async def progress(processed: int, total: int) -> None:
            assert job is not None
            job.processed, job.total = processed, total

        try:
            result = await service.rebuild_release_embeddings(
                db,
                settings=settings,
                release_id=job.release_id,
                batch_size=options["batch_size"],
                rebuild_all=options["rebuild_all"],
                on_batch=progress,
            )
            release = await db.get(KnowledgeRelease, job.release_id)
            if release:
                report = json.loads(release.quality_report_json)
                report["embedding_fingerprint"] = fingerprint(settings)
                report["embedding_model"] = settings.embedding_model
                report.pop("requires_full_rebuild", None)
                release.quality_report_json = json.dumps(report, ensure_ascii=False)
            job.status = "completed"
            job.detail_json = json.dumps({**options, **asdict(result)})
        except Exception as exc:  # noqa: BLE001 - job boundary must persist failure and release its slot
            await db.rollback()
            job = await db.get(KnowledgeOperation, job_id)
            assert job is not None
            job.status = "failed"
            # Persist a safe code, never provider response bodies or credentials.
            job.error = (
                str(exc)
                if str(exc).startswith(("embedding_", "knowledge_"))
                else type(exc).__name__
            )
            job.error = job.error[:255]
            logger.warning("knowledge_job_failed", job_id=job_id, error=job.error)
        job.active_slot = None
        job.finished_at = datetime.now(UTC)
        await db.commit()


async def search(db: AsyncSession, payload: SearchRequest, settings: Settings) -> dict[str, Any]:
    await require_release(db, payload.release_id)
    options = settings.model_copy(
        update={
            "rag_top_k": payload.top_k,
            "rag_min_score": payload.min_score,
            "rag_relative_score": payload.relative_score,
        }
    )
    result = await retriever.retrieve_detailed(
        db,
        query=payload.query,
        settings=options,
        release_id=payload.release_id,
        lexical_only=payload.mode == "lexical",
    )
    return {
        "query": payload.query,
        "mode": result.mode,
        "warnings": result.warnings,
        "latency_ms": result.latency_ms,
        "hits": [{**asdict(hit), **result.scores[hit.entry_id]} for hit in result.hits],
    }


def _first_alias(value: str) -> str:
    """Return one authored alternate question without inventing test data."""
    return next((item.strip() for item in re.split(r"[\r\n;；|]+", value) if item.strip()), "")


async def generate_evaluation_cases(
    db: AsyncSession,
    *,
    release_id: str,
    limit: int,
    source: str,
) -> dict[str, Any]:
    await require_release(db, release_id)
    entries = list(
        await db.scalars(
            select(KnowledgeEntry)
            .where(KnowledgeEntry.release_id == release_id)
            .order_by(
                KnowledgeEntry.category,
                KnowledgeEntry.source_sheet,
                KnowledgeEntry.source_row,
                KnowledgeEntry.id,
            )
        )
    )
    by_category: dict[str, list[KnowledgeEntry]] = {}
    for entry in entries:
        alias = _first_alias(entry.aliases)
        if source == "aliases" and not alias:
            continue
        by_category.setdefault(entry.category, []).append(entry)

    selected: list[KnowledgeEntry] = []
    categories = sorted(by_category)
    offset = 0
    while len(selected) < limit:
        added = False
        for category in categories:
            rows = by_category[category]
            if offset < len(rows):
                selected.append(rows[offset])
                added = True
                if len(selected) == limit:
                    break
        if not added:
            break
        offset += 1

    cases: list[dict[str, Any]] = []
    alias_count = 0
    for entry in selected:
        alias = _first_alias(entry.aliases)
        use_alias = source != "standard" and bool(alias)
        alias_count += int(use_alias)
        cases.append(
            {
                "query": alias if use_alias else entry.question,
                "expected_entry_ids": [entry.id],
                "expect_no_results": False,
                "query_source": "alias" if use_alias else "standard",
                "category": entry.category,
                "standard_question": entry.question,
            }
        )
    return {
        "release_id": release_id,
        "cases": cases,
        "generated_count": len(cases),
        "alias_count": alias_count,
        "standard_count": len(cases) - alias_count,
        "available_entries": len(entries),
        "available_alias_entries": sum(bool(_first_alias(entry.aliases)) for entry in entries),
        "category_count": len({entry.category for entry in selected}),
    }


async def evaluate(
    db: AsyncSession, payload: EvaluationRequest, settings: Settings
) -> KnowledgeOperation:
    await require_release(db, payload.release_id)
    valid_ids = set(
        await db.scalars(
            select(KnowledgeEntry.id).where(KnowledgeEntry.release_id == payload.release_id)
        )
    )
    if any(set(case.expected_entry_ids) - valid_ids for case in payload.cases):
        raise HTTPException(422, "expected_entry_not_in_release")
    rows: list[dict[str, Any]] = []
    recalls: list[float] = []
    ranks: list[float] = []
    successes: list[bool] = []
    no_result_success: list[bool] = []
    for case in payload.cases:
        result = await search(db, payload.model_copy(update={"query": case.query}), settings)
        ids = [hit["entry_id"] for hit in result["hits"]]
        expected = set(case.expected_entry_ids)
        rank = next((i for i, entry_id in enumerate(ids, 1) if entry_id in expected), None)
        if case.expect_no_results:
            passed = not ids
            no_result_success.append(passed)
        else:
            passed = rank is not None
            successes.append(passed)
            recalls.append(len(expected & set(ids)) / len(expected))
            ranks.append(1 / rank if rank else 0)
        rows.append({**case.model_dump(), **result, "passed": passed, "first_match_rank": rank})

    def mean(values: list[Any]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    detail = {
        "config": payload.model_dump(exclude={"cases", "query"}),
        "cases": rows,
        "metrics": {
            "hit_rate": mean(successes),
            "recall_at_k": mean(recalls),
            "mrr": mean(ranks),
            "no_result_accuracy": mean(no_result_success),
            "pass_rate": mean([row["passed"] for row in rows]),
            "mean_latency_ms": mean([row["latency_ms"] for row in rows]),
        },
    }
    op = audit(db, payload.release_id, "evaluation", **detail)
    op.total = len(rows)
    op.processed = len(rows)
    await db.commit()
    return op
