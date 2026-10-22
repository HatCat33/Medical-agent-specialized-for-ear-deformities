"""FAQ release import, publish, retrieval context, and citation use cases."""

import hashlib
import json
import math
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.core.config import Settings
from medical_agent.core.logging import get_logger
from medical_agent.modules.ingestion.faq_xlsx import parse_faq_xlsx
from medical_agent.modules.knowledge import repository, retriever
from medical_agent.modules.knowledge.models import KnowledgeEntry, KnowledgeRelease
from medical_agent.modules.knowledge.schemas import FaqEntryInput, RetrievalHit
from medical_agent.providers.embedding.client import (
    EmbeddingProviderError,
    build_embedding_provider,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class EmbeddingRebuildResult:
    release_id: str
    release_name: str
    total_entries: int
    existing_entries: int
    processed_entries: int
    remaining_entries: int
    vector_dimension: int | None


def embedding_text(entry: FaqEntryInput | KnowledgeEntry) -> str:
    return f"问题：{entry.question}\n其他问法：{entry.aliases}\n答案：{entry.answer}"


def _stored_vector_dimension(entries: list[KnowledgeEntry]) -> int | None:
    for entry in entries:
        if not entry.embedding_json:
            continue
        try:
            vector = cast(object, json.loads(entry.embedding_json))
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(vector, list) and vector:
            return len(cast(list[object], vector))
    return None


def question_key(value: str) -> str:
    return "".join(value.casefold().split())


def deduplicate_faq_entries(
    entries: list[FaqEntryInput],
) -> tuple[list[FaqEntryInput], list[list[int]]]:
    groups: dict[str, list[FaqEntryInput]] = defaultdict(list)
    for entry in entries:
        groups[question_key(entry.question)].append(entry)
    accepted: list[FaqEntryInput] = []
    conflicts: list[list[int]] = []
    for group in groups.values():
        answers = {"".join(item.answer.split()) for item in group}
        if len(answers) > 1:
            conflicts.append([item.source_row for item in group])
            continue
        accepted.append(group[0])
    return accepted, conflicts


async def import_faq_release(
    db: AsyncSession,
    *,
    workbook_path: Path,
    release_name: str,
    publish: bool,
    exclude_conflicts: bool,
    settings: Settings,
) -> KnowledgeRelease:
    source_bytes = workbook_path.read_bytes()
    checksum = hashlib.sha256(source_bytes).hexdigest()
    existing = await repository.get_release_by_checksum(db, checksum=checksum)
    if existing is not None:
        if publish and existing.status != "published":
            await repository.archive_published_releases(db)
            existing.status = "published"
            existing.published_at = datetime.now(UTC)
            await db.commit()
        return existing
    entries, profile = parse_faq_xlsx(workbook_path)
    accepted, conflicts = deduplicate_faq_entries(entries)
    if conflicts and not exclude_conflicts:
        row_text = ", ".join("/".join(str(row) for row in rows) for rows in conflicts)
        raise ValueError(f"faq_conflicting_duplicate_answers:{row_text}")
    quality_report = {
        "source_rows": profile.source_rows,
        "accepted_entries": len(accepted),
        "exact_duplicate_groups": sum(
            1 for duplicate in profile.duplicate_questions if duplicate.rows not in conflicts
        ),
        "excluded_conflicting_rows": conflicts,
        "invalid_effective_date_rows": profile.invalid_effective_date_rows,
        "warnings": profile.warnings,
    }
    release = KnowledgeRelease(
        id=str(uuid.uuid4()),
        name=release_name,
        source_name=workbook_path.name,
        source_checksum=checksum,
        status="draft",
        entry_count=len(accepted),
        quality_report_json=json.dumps(quality_report, ensure_ascii=False, separators=(",", ":")),
    )
    db.add(release)
    await db.flush()

    embedding_provider = build_embedding_provider(settings)
    vectors: list[list[float] | None] = [None] * len(accepted)
    if embedding_provider is not None:
        for start in range(0, len(accepted), settings.embedding_batch_size):
            batch = accepted[start : start + settings.embedding_batch_size]
            texts = [embedding_text(entry) for entry in batch]
            embedded = await embedding_provider.embed(texts)
            vectors[start : start + len(batch)] = embedded

    for entry, vector in zip(accepted, vectors, strict=True):
        db.add(
            KnowledgeEntry(
                id=str(uuid.uuid4()),
                release_id=release.id,
                category=entry.category,
                question=entry.question,
                aliases=entry.aliases,
                answer=entry.answer,
                effective_date=entry.effective_date,
                source_sheet=entry.source_sheet,
                source_row=entry.source_row,
                search_text=f"{entry.question} {entry.aliases} {entry.answer}",
                embedding_json=json.dumps(vector, separators=(",", ":")) if vector else None,
            )
        )
    if publish:
        await repository.archive_published_releases(db)
        release.status = "published"
        release.published_at = datetime.now(UTC)
    await db.commit()
    logger.info(
        "knowledge_release_imported",
        release_id=release.id,
        status=release.status,
        entry_count=release.entry_count,
        source_rows=profile.source_rows,
        duplicate_count=len(profile.duplicate_questions),
        invalid_effective_date_count=len(profile.invalid_effective_date_rows),
        excluded_conflicting_rows=conflicts,
    )
    return release


async def rebuild_release_embeddings(
    db: AsyncSession,
    *,
    settings: Settings,
    release_id: str | None = None,
    batch_size: int | None = None,
    rebuild_all: bool = False,
    on_batch: Callable[[int, int], Awaitable[None]] | None = None,
) -> EmbeddingRebuildResult:
    release = (
        await repository.get_release_by_id(db, release_id=release_id)
        if release_id
        else await repository.get_published_release(db)
    )
    if release is None:
        raise ValueError("knowledge_release_not_found")

    entries = sorted(
        release.entries,
        key=lambda entry: (entry.source_sheet, entry.source_row, entry.id),
    )
    pending = entries if rebuild_all else [entry for entry in entries if not entry.embedding_json]
    existing_entries = len(entries) - len(pending)
    dimension = None if rebuild_all else _stored_vector_dimension(entries)
    if not pending:
        return EmbeddingRebuildResult(
            release_id=release.id,
            release_name=release.name,
            total_entries=len(entries),
            existing_entries=existing_entries,
            processed_entries=0,
            remaining_entries=0,
            vector_dimension=dimension,
        )

    embedding_provider = build_embedding_provider(settings)
    if embedding_provider is None:
        raise ValueError("embedding_not_configured")
    effective_batch_size = batch_size or settings.embedding_batch_size
    if not 1 <= effective_batch_size <= 256:
        raise ValueError("embedding_batch_size_invalid")

    processed = 0
    for start in range(0, len(pending), effective_batch_size):
        batch = pending[start : start + effective_batch_size]
        vectors = await embedding_provider.embed([embedding_text(entry) for entry in batch])
        batch_dimensions = {len(vector) for vector in vectors if vector}
        if (
            len(batch_dimensions) != 1
            or len(vectors) != len(batch)
            or any(not vector or not all(math.isfinite(v) for v in vector) for vector in vectors)
        ):
            raise EmbeddingProviderError("embedding_dimension_invalid")
        batch_dimension = batch_dimensions.pop()
        if dimension is not None and batch_dimension != dimension:
            raise EmbeddingProviderError("embedding_dimension_mismatch")
        dimension = batch_dimension

        for entry, vector in zip(batch, vectors, strict=True):
            entry.embedding_json = json.dumps(vector, separators=(",", ":"))
        processed += len(batch)
        if on_batch is not None:
            await on_batch(processed, len(pending))
        await db.commit()
        logger.info(
            "knowledge_embeddings_batch_saved",
            release_id=release.id,
            processed_entries=processed,
            target_entries=len(pending),
            remaining_entries=len(pending) - processed,
            vector_dimension=dimension,
        )

    return EmbeddingRebuildResult(
        release_id=release.id,
        release_name=release.name,
        total_entries=len(entries),
        existing_entries=existing_entries,
        processed_entries=processed,
        remaining_entries=len(pending) - processed,
        vector_dimension=dimension,
    )


async def retrieve_context(
    db: AsyncSession,
    *,
    query: str,
    settings: Settings,
) -> tuple[str, list[RetrievalHit]]:
    hits = await retriever.retrieve(db, query=query, settings=settings)
    if not hits:
        return "", []
    blocks = [
        f"[{index}] 标准问题：{hit.question}\n审核答案：{hit.answer}\n分类：{hit.category}"
        for index, hit in enumerate(hits, start=1)
    ]
    context = (
        "以下内容来自医院提供并发布的知识库。只根据这些资料回答事实性问题；"
        "不要扩展资料中没有的诊断、治疗或医院政策。引用时使用 [1]、[2]。\n\n" + "\n\n".join(blocks)
    )
    return context, hits


def citation_markdown(hits: list[RetrievalHit]) -> str:
    if not hits:
        return ""
    lines = ["\n\n参考资料："]
    lines.extend(f"- [{index}] {hit.citation_label}" for index, hit in enumerate(hits, start=1))
    return "\n".join(lines)
