import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from medical_agent.core.config import Settings
from medical_agent.core.database import Base
from medical_agent.modules.knowledge import service as knowledge_service
from medical_agent.modules.knowledge.models import KnowledgeEntry, KnowledgeRelease
from medical_agent.modules.knowledge.retriever import retrieve
from medical_agent.modules.knowledge.schemas import FaqEntryInput
from medical_agent.modules.knowledge.service import (
    citation_markdown,
    deduplicate_faq_entries,
    rebuild_release_embeddings,
)


def faq_entry(*, row: int, answer: str = "请携带既往检查资料。") -> FaqEntryInput:
    return FaqEntryInput(
        category="就诊准备",
        question="第一次就诊需要准备什么？",
        aliases="初诊带什么",
        answer=answer,
        effective_date="",
        source_sheet="FAQ",
        source_row=row,
    )


def test_deduplicate_rejects_conflicting_answers() -> None:
    accepted, conflicts = deduplicate_faq_entries(
        [faq_entry(row=2), faq_entry(row=3, answer="无需准备资料。")]
    )

    assert accepted == []
    assert conflicts == [[2, 3]]


@pytest.mark.asyncio
async def test_keyword_retrieval_uses_published_release(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'knowledge.db'}")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    release_id = str(uuid.uuid4())
    async with session_maker() as db:
        db.add(
            KnowledgeRelease(
                id=release_id,
                name="测试发布",
                source_name="FAQ.xlsx",
                source_checksum="a" * 64,
                status="published",
                entry_count=1,
                published_at=datetime.now(UTC),
            )
        )
        db.add(
            KnowledgeEntry(
                id=str(uuid.uuid4()),
                release_id=release_id,
                category="就诊准备",
                question="第一次就诊需要准备什么？",
                aliases="初诊带什么",
                answer="请携带既往检查资料。",
                effective_date="",
                source_sheet="FAQ",
                source_row=2,
                search_text="第一次就诊需要准备什么 初诊带什么 请携带既往检查资料",
            )
        )
        await db.commit()

        hits = await retrieve(
            db,
            query="初诊要带什么资料？",
            settings=Settings(rag_enabled=True, rag_min_score=0.1),
        )

    await engine.dispose()
    assert len(hits) == 1
    assert hits[0].question == "第一次就诊需要准备什么？"
    assert "[1] 测试发布" in citation_markdown(hits)


@pytest.mark.asyncio
async def test_embedding_rebuild_only_backfills_missing_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'embeddings.db'}")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    release_id = str(uuid.uuid4())
    entry_ids = [str(uuid.uuid4()) for _ in range(3)]
    async with session_maker() as db:
        release = KnowledgeRelease(
            id=release_id,
            name="向量补全测试",
            source_name="FAQ.xlsx",
            source_checksum="c" * 64,
            status="published",
            entry_count=3,
            published_at=datetime.now(UTC),
        )
        db.add(release)
        for index, entry_id in enumerate(entry_ids, start=1):
            db.add(
                KnowledgeEntry(
                    id=entry_id,
                    release_id=release_id,
                    category="就诊准备",
                    question=f"测试问题 {index}",
                    aliases="其他问法",
                    answer=f"测试答案 {index}",
                    effective_date="",
                    source_sheet="FAQ",
                    source_row=index + 1,
                    search_text=f"测试问题 {index} 测试答案 {index}",
                    embedding_json="[9.0,9.0]" if index == 1 else None,
                )
            )
        await db.commit()

    embedded_batches: list[list[str]] = []

    class FakeEmbeddingProvider:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            embedded_batches.append(texts)
            return [[float(index), 1.0] for index, _ in enumerate(texts, start=1)]

    def build_fake_embedding_provider(_: Settings) -> FakeEmbeddingProvider:
        return FakeEmbeddingProvider()

    monkeypatch.setattr(
        knowledge_service,
        "build_embedding_provider",
        build_fake_embedding_provider,
    )
    settings = Settings(rag_enabled=True, embedding_batch_size=1)

    async with session_maker() as db:
        result = await rebuild_release_embeddings(db, settings=settings)
        rerun = await rebuild_release_embeddings(db, settings=settings)
        stored_entries = list(
            await db.scalars(select(KnowledgeEntry).order_by(KnowledgeEntry.source_row))
        )

    await engine.dispose()
    assert result.total_entries == 3
    assert result.existing_entries == 1
    assert result.processed_entries == 2
    assert result.remaining_entries == 0
    assert result.vector_dimension == 2
    assert rerun.processed_entries == 0
    assert len(embedded_batches) == 2
    assert stored_entries[0].embedding_json == "[9.0,9.0]"
    assert all(entry.embedding_json is not None for entry in stored_entries)
