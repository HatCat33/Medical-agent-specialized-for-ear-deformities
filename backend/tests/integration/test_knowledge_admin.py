import io
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from medical_agent.api import dependencies
from medical_agent.api.router import api_router
from medical_agent.core.config import Settings
from medical_agent.core.database import Base, get_db
from medical_agent.modules.conversation.models import Conversation, Message
from medical_agent.modules.knowledge import admin_router, retriever, service
from medical_agent.modules.knowledge.models import (
    KnowledgeEntry,
    KnowledgeOperation,
    KnowledgeRelease,
)
from medical_agent.providers.embedding.client import EmbeddingProviderError

PREFIX = "/api/v1/admin/knowledge"
ENTRY = {
    "category": "就诊",
    "question": "初诊需要带什么资料",
    "answer": "携带既往检查资料",
    "aliases": "第一次来",
    "effective_date": "",
}


class FakeEmbedding:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.5] for _ in texts]


def fake_embedding(_: Settings) -> FakeEmbedding:
    return FakeEmbedding()


@pytest.fixture
async def admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        admin_api_token="test-admin",
        rag_enabled=True,
        embedding_model="test-model",
        embedding_base_url="http://fake",
        embedding_api_key="fake",
    )
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(admin_router, "get_settings", lambda: settings)
    monkeypatch.setattr(admin_router, "session_factory", factory)
    monkeypatch.setattr(service, "build_embedding_provider", fake_embedding)
    monkeypatch.setattr(retriever, "build_embedding_provider", fake_embedding)

    async def database() -> AsyncIterator[AsyncSession]:
        async with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_db] = database
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Admin-Token": "test-admin"},
    ) as client:
        yield client, factory
    await engine.dispose()


async def draft(client: AsyncClient, name: str = "测试草稿") -> str:
    response = await client.post(f"{PREFIX}/releases", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def add(client: AsyncClient, release_id: str, **changes: str) -> dict[str, Any]:
    response = await client.post(
        f"{PREFIX}/releases/{release_id}/entries", json={**ENTRY, **changes}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_auth_empty_database_and_validation(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = admin
    for path in ("/overview", "/releases", "/operations"):
        assert (
            await client.get(PREFIX + path, headers={"X-Admin-Token": "wrong"})
        ).status_code == 401
    assert (await client.get(PREFIX + "/overview")).status_code == 200
    assert (await client.get(PREFIX + "/releases")).json() == []
    release_id = await draft(client)
    assert (await client.post(f"{PREFIX}/releases/{release_id}/publish")).status_code == 409
    assert (
        await client.post(
            f"{PREFIX}/releases/{release_id}/entries", json={**ENTRY, "question": "   "}
        )
    ).status_code == 422


@pytest.mark.asyncio
async def test_overview_counts_each_user_message_as_one_interaction(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = admin
    async with factory() as db:
        conversation = Conversation(id="conversation-1", owner_id="visitor-1", title="测试会话")
        db.add(conversation)
        db.add_all(
            [
                Message(
                    id="message-user-1",
                    conversation_id=conversation.id,
                    sequence_no=1,
                    role="user",
                    content="第一次提问",
                ),
                Message(
                    id="message-assistant-1",
                    conversation_id=conversation.id,
                    sequence_no=2,
                    role="assistant",
                    content="第一次回复",
                ),
                Message(
                    id="message-user-2",
                    conversation_id=conversation.id,
                    sequence_no=3,
                    role="user",
                    content="第二次提问",
                ),
            ]
        )
        await db.commit()

    overview = await client.get(PREFIX + "/overview")
    assert overview.status_code == 200
    assert overview.json()["interaction_count"] == 2


@pytest.mark.asyncio
async def test_draft_vector_reuse_edit_delete_publish_and_rollback(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = admin
    original = await draft(client)
    first = await add(client, original)
    await add(client, original, question="如何预约门诊")
    built = await client.post(f"{PREFIX}/releases/{original}/jobs", json={"batch_size": 1})
    assert built.status_code == 202, built.text
    job = (await client.get(f"{PREFIX}/operations/{built.json()['id']}")).json()
    assert job["status"] == "completed" and job["processed"] == 2
    assert (await client.post(f"{PREFIX}/releases/{original}/publish")).status_code == 200
    assert (
        await client.delete(f"{PREFIX}/releases/{original}/entries/{first['id']}")
    ).status_code == 409
    assert (
        await client.post(f"{PREFIX}/releases/{original}/jobs", json={"rebuild_all": True})
    ).status_code == 409
    clone = (
        await client.post(
            f"{PREFIX}/releases", json={"name": "增量草稿", "base_release_id": original}
        )
    ).json()["id"]
    rows = (await client.get(f"{PREFIX}/releases/{clone}/entries")).json()["items"]
    assert len(rows) == 2 and all(r["vectorized"] for r in rows)
    edited = await client.put(
        f"{PREFIX}/releases/{clone}/entries/{rows[0]['id']}",
        json={**ENTRY, "answer": "携带身份证和既往检查资料"},
    )
    assert edited.status_code == 200 and not edited.json()["vectorized"]
    pending = (await client.get(f"{PREFIX}/releases/{clone}/entries?vector=pending")).json()
    assert pending["total"] == 1
    assert (
        await client.delete(f"{PREFIX}/releases/{clone}/entries/{rows[1]['id']}")
    ).status_code == 200
    assert (await client.post(f"{PREFIX}/releases/{clone}/publish")).status_code == 200
    assert (await client.post(f"{PREFIX}/releases/{original}/publish")).status_code == 200
    async with factory() as db:
        assert (
            len(
                list(
                    await db.scalars(
                        select(KnowledgeRelease).where(KnowledgeRelease.status == "published")
                    )
                )
            )
            == 1
        )
        assert (await db.get(KnowledgeEntry, first["id"])) is not None
    records = (await client.get(PREFIX + "/operations")).json()["items"]
    assert any(r["kind"] == "entry_deleted" for r in records)


def workbook(rows: list[list[str]]) -> bytes:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["分类", "标准问题", "答案内容", "其他问法"])
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_incremental_import_is_idempotent_and_preserves_unmentioned_entries(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = admin
    release_id = await draft(client)
    await add(client, release_id)
    await add(client, release_id, question="旧文件保留条目")
    await client.post(f"{PREFIX}/releases/{release_id}/jobs", json={})
    data = workbook(
        [
            ["就诊", ENTRY["question"], "新审核答案", "第一次来"],
            ["术后", "复诊要带什么", "复诊资料", ""],
        ]
    )
    url = f"{PREFIX}/releases/{release_id}/import"
    first = await client.post(url, files={"file": ("FAQ.xlsx", data)})
    assert first.status_code == 200, first.text
    assert first.json()["added"] == 1 and first.json()["updated"] == 1
    second = (await client.post(url, files={"file": ("FAQ.xlsx", data)})).json()
    assert second["added"] == second["updated"] == 0 and second["unchanged"] == 2
    rows = (await client.get(f"{PREFIX}/releases/{release_id}/entries")).json()["items"]
    assert len(rows) == 3
    assert next(r for r in rows if r["question"] == "旧文件保留条目")["vectorized"]
    conflict = workbook([["a", "同一问题", "答案一", ""], ["a", "同一问题", "答案二", ""]])
    assert (await client.post(url, files={"file": ("FAQ.xlsx", conflict)})).status_code == 422
    assert (await client.post(url, files={"file": ("FAQ.xlsx", b"not-xlsx")})).status_code == 422


@pytest.mark.asyncio
async def test_evaluation_metrics_and_invalid_labels(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = admin
    release_id = await draft(client)
    entry = await add(client, release_id)
    payload = {
        "release_id": release_id,
        "mode": "lexical",
        "min_score": 0.9,
        "cases": [
            {"query": ENTRY["question"], "expected_entry_ids": [entry["id"]]},
            {"query": "zzzzzz", "expect_no_results": True},
        ],
    }
    response = await client.post(PREFIX + "/evaluations", json=payload)
    assert response.status_code == 200, response.text
    detail = response.json()["detail"]
    assert (
        detail["metrics"]["hit_rate"]
        == detail["metrics"]["recall_at_k"]
        == detail["metrics"]["mrr"]
        == 1
    )
    assert detail["metrics"]["no_result_accuracy"] == 1
    assert (await client.get(PREFIX + "/operations?kind=evaluation")).json()["total"] == 1
    payload["cases"] = [{"query": "test", "expected_entry_ids": ["foreign-id"]}]
    assert (await client.post(PREFIX + "/evaluations", json=payload)).status_code == 422
    payload["cases"] = [{"query": "test"}]
    assert (await client.post(PREFIX + "/evaluations", json=payload)).status_code == 422


@pytest.mark.asyncio
async def test_generate_balanced_evaluation_cases_without_model_calls(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = admin
    release_id = await draft(client)
    first = await add(client, release_id, question="标准问题一", aliases="患者问法一；备用问法")
    second = await add(client, release_id, question="标准问题二", category="术后", aliases="")

    mixed = await client.post(
        PREFIX + "/evaluation-cases/generate",
        json={"release_id": release_id, "limit": 20, "source": "mixed"},
    )
    assert mixed.status_code == 200, mixed.text
    result = mixed.json()
    assert result["generated_count"] == 2
    assert result["alias_count"] == 1
    assert result["category_count"] == 2
    assert result["cases"][0]["expected_entry_ids"] in ([first["id"]], [second["id"]])
    assert {case["query"] for case in result["cases"]} == {"患者问法一", "标准问题二"}

    aliases = await client.post(
        PREFIX + "/evaluation-cases/generate",
        json={"release_id": release_id, "limit": 100, "source": "aliases"},
    )
    assert aliases.status_code == 200
    assert aliases.json()["generated_count"] == 1
    assert aliases.json()["cases"][0]["expected_entry_ids"] == [first["id"]]


@pytest.mark.asyncio
async def test_job_failure_keeps_batches_and_incremental_retry(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, factory = admin
    release_id = await draft(client)
    await add(client, release_id)
    await add(client, release_id, question="第二个问题")

    class FailingEmbedding:
        calls = 0

        async def embed(self, texts: list[str]) -> list[list[float]]:
            self.calls += 1
            if self.calls == 2:
                raise EmbeddingProviderError("embedding_request_failed")
            return [[1.0, 0.5] for _ in texts]

    provider = FailingEmbedding()

    def failing_embedding(_: Settings) -> FailingEmbedding:
        return provider

    monkeypatch.setattr(service, "build_embedding_provider", failing_embedding)
    response = await client.post(f"{PREFIX}/releases/{release_id}/jobs", json={"batch_size": 1})
    job = (await client.get(f"{PREFIX}/operations/{response.json()['id']}")).json()
    assert job["status"] == "failed" and job["processed"] == 1
    assert (await client.get(f"{PREFIX}/releases/{release_id}/entries?vector=ready")).json()[
        "total"
    ] == 1
    monkeypatch.setattr(service, "build_embedding_provider", fake_embedding)
    retry = await client.post(f"{PREFIX}/releases/{release_id}/jobs", json={"batch_size": 1})
    retried = (await client.get(f"{PREFIX}/operations/{retry.json()['id']}")).json()
    assert retried["status"] == "completed" and retried["processed"] == 1
    async with factory() as db:
        assert not list(
            await db.scalars(
                select(KnowledgeOperation).where(KnowledgeOperation.active_slot.is_not(None))
            )
        )


@pytest.mark.asyncio
async def test_active_job_blocks_edits_and_duplicate_jobs(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = admin
    release_id = await draft(client)
    entry = await add(client, release_id)
    async with factory() as db:
        db.add(
            KnowledgeOperation(
                id="occupied",
                release_id=release_id,
                kind="embedding",
                status="running",
                active_slot=release_id,
            )
        )
        await db.commit()
    assert (await client.post(f"{PREFIX}/releases/{release_id}/jobs", json={})).status_code == 409
    assert (
        await client.delete(f"{PREFIX}/releases/{release_id}/entries/{entry['id']}")
    ).status_code == 409
    assert (await client.post(f"{PREFIX}/releases/{release_id}/publish")).status_code == 409


@pytest.mark.asyncio
async def test_model_mismatch_and_incomplete_full_rebuild_block_publication(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = admin
    release_id = await draft(client)
    await add(client, release_id)
    async with factory() as db:
        release = await db.get(KnowledgeRelease, release_id)
        assert release is not None
        release.quality_report_json = json.dumps(
            {"embedding_fingerprint": "other-model", "requires_full_rebuild": True}
        )
        await db.commit()
    assert (await client.post(f"{PREFIX}/releases/{release_id}/jobs", json={})).status_code == 409
    assert (await client.post(f"{PREFIX}/releases/{release_id}/publish")).status_code == 409
    response = await client.post(f"{PREFIX}/releases/{release_id}/jobs", json={"rebuild_all": True})
    assert response.status_code == 202
    assert (await client.post(f"{PREFIX}/releases/{release_id}/publish")).status_code == 200


@pytest.mark.asyncio
async def test_hybrid_fallback_is_visible(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = admin
    release_id = await draft(client)
    await add(client, release_id)
    await client.post(f"{PREFIX}/releases/{release_id}/jobs", json={})

    class FailingQuery:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise EmbeddingProviderError("embedding_request_failed")

    def failing_query(_: Settings) -> FailingQuery:
        return FailingQuery()

    monkeypatch.setattr(retriever, "build_embedding_provider", failing_query)
    response = await client.post(
        PREFIX + "/search", json={"release_id": release_id, "query": ENTRY["question"]}
    )
    result = response.json()
    assert result["mode"] == "lexical" and result["hits"]
    assert "embedding_request_failed_using_lexical" in result["warnings"]
