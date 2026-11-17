import io
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from medical_agent.api import dependencies
from medical_agent.api.router import api_router
from medical_agent.core.admin_security import issue_secondary_grant
from medical_agent.core.config import Settings
from medical_agent.core.database import Base, get_db
from medical_agent.modules.conversation import service as conversation_service
from medical_agent.modules.conversation.models import Conversation, Message
from medical_agent.modules.knowledge import admin_router, retriever, service
from medical_agent.modules.knowledge.models import (
    KnowledgeEntry,
    KnowledgeOperation,
    KnowledgeRelease,
)
from medical_agent.modules.system import counter_service
from medical_agent.modules.system.models import SystemCounter
from medical_agent.providers.embedding.client import EmbeddingProviderError
from medical_agent.providers.llm.protocol import ChatTurn
from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


@pytest_asyncio.fixture
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
        admin_secondary_token="test-secondary",
        rag_enabled=True,
        embedding_model="test-model",
        embedding_base_url="http://fake",
        embedding_api_key="fake",
    )
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(admin_router, "get_settings", lambda: settings)
    monkeypatch.setattr(admin_router, "session_factory", factory)
    monkeypatch.setattr(conversation_service, "get_settings", lambda: settings)
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
        headers={
            "X-Admin-Token": "test-admin",
            "X-Admin-Authorization": issue_secondary_grant(settings)[0],
        },
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
async def test_knowledge_drafts_are_bounded(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = admin
    for index in range(3):
        assert (
            await client.post(f"{PREFIX}/releases", json={"name": f"草稿 {index + 1}"})
        ).status_code == 201

    rejected = await client.post(f"{PREFIX}/releases", json={"name": "第四个草稿"})

    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "knowledge_draft_limit_reached"


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
async def test_overview_uses_monotonic_interaction_counter_after_conversation_deletion(
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
        db.add(SystemCounter(key="patient_interactions", value=2))
        await db.commit()

        await db.delete(conversation)
        await db.commit()

    overview = await client.get(PREFIX + "/overview")
    assert overview.status_code == 200
    assert overview.json()["interaction_count"] == 2


@pytest.mark.asyncio
async def test_patient_interaction_counter_increments_without_counting_admin_tests(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = admin
    async with factory() as db:
        conversation = Conversation(id="counter-conversation", owner_id="counter-owner", title="计数")
        db.add(conversation)
        db.add(Message(id="counter-user-1", conversation_id=conversation.id, sequence_no=1, role="user", content="一"))
        await db.flush()
        await counter_service.increment(db, counter_service.PATIENT_INTERACTIONS)
        db.add(Message(id="counter-user-2", conversation_id=conversation.id, sequence_no=2, role="user", content="二"))
        await db.flush()
        await counter_service.increment(db, counter_service.PATIENT_INTERACTIONS)
        await db.commit()

    overview = await client.get(PREFIX + "/overview")
    assert overview.status_code == 200
    assert overview.json()["interaction_count"] == 2


@pytest.mark.asyncio
async def test_overview_falls_back_to_existing_messages_before_counter_initialization(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = admin
    async with factory() as db:
        conversation = Conversation(id="legacy-conversation", owner_id="legacy-owner", title="历史")
        db.add(conversation)
        db.add_all([
            Message(id="legacy-user-1", conversation_id=conversation.id, sequence_no=1, role="user", content="一"),
            Message(id="legacy-assistant-1", conversation_id=conversation.id, sequence_no=2, role="assistant", content="答"),
            Message(id="legacy-user-2", conversation_id=conversation.id, sequence_no=3, role="user", content="二"),
        ])
        await db.commit()

    overview = await client.get(PREFIX + "/overview")
    assert overview.status_code == 200
    assert overview.json()["interaction_count"] == 2


@pytest.mark.asyncio
async def test_interaction_analytics_filters_by_date_and_formatted_user_id(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = admin
    first_owner = "analytics-owner-first"
    second_owner = "analytics-owner-second"
    async with factory() as db:
        first = Conversation(id="analytics-conversation-1", owner_id=first_owner, title="第一位用户")
        second = Conversation(id="analytics-conversation-2", owner_id=second_owner, title="第二位用户")
        db.add_all([first, second])
        db.add_all([
            Message(id="analytics-user-1", conversation_id=first.id, sequence_no=1, role="user", content="第一个问题", created_at=datetime(2026, 11, 10, 2, 0, tzinfo=UTC)),
            Message(id="analytics-assistant-1", conversation_id=first.id, sequence_no=2, role="assistant", content="第一个回答", created_at=datetime(2026, 11, 10, 2, 1, tzinfo=UTC)),
            Message(id="analytics-user-2", conversation_id=first.id, sequence_no=3, role="user", content="第二个问题", created_at=datetime(2026, 11, 11, 3, 0, tzinfo=UTC)),
            Message(id="analytics-assistant-2", conversation_id=first.id, sequence_no=4, role="assistant", content="第二个回答", created_at=datetime(2026, 11, 11, 3, 1, tzinfo=UTC)),
            Message(id="analytics-user-3", conversation_id=second.id, sequence_no=1, role="user", content="另一个用户问题", created_at=datetime(2026, 11, 11, 4, 0, tzinfo=UTC)),
            Message(id="analytics-assistant-3", conversation_id=second.id, sequence_no=2, role="assistant", content="另一个用户回答", created_at=datetime(2026, 11, 11, 4, 1, tzinfo=UTC)),
        ])
        await db.commit()

    reference = dependencies.owner_reference(first_owner)
    formatted = "-".join(reference.upper()[index:index + 4] for index in range(0, 16, 4))
    response = await client.get(
        PREFIX + "/interaction-analytics",
        params={"start_date": "2026-11-11", "end_date": "2026-11-11", "user_id": formatted},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["summary"] == {
        "total_users": 2,
        "matched_users": 1,
        "matched_interactions": 1,
        "average_interactions": 1.0,
    }
    assert payload["items"][0]["input"] == "第二个问题"
    assert payload["items"][0]["output"] == "第二个回答"
    assert payload["items"][0]["user_id"] == formatted
    assert first_owner not in response.text
    assert second_owner not in response.text
    assert len(payload["trend"]) == 1
    assert payload["trend"][0]["key"] == "2026-11-11"
    assert sum(item["interactions"] for item in payload["trend"]) == 1


@pytest.mark.asyncio
async def test_interaction_analytics_rejects_invalid_filters(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = admin
    assert (
        await client.get(PREFIX + "/interaction-analytics", params={"user_id": "invalid"})
    ).status_code == 422


@pytest.mark.asyncio
async def test_interaction_analytics_limits_long_range_to_fifteen_buckets(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = admin
    async with factory() as db:
        conversation = Conversation(
            id="analytics-long-range", owner_id="analytics-long-owner", title="长期趋势"
        )
        db.add(conversation)
        db.add_all([
            Message(
                id=f"analytics-long-{index}",
                conversation_id=conversation.id,
                sequence_no=index + 1,
                role="user",
                content=f"第 {index + 1} 个问题",
                created_at=datetime(2026, month, 5, 2, 0, tzinfo=UTC),
            )
            for index, month in enumerate(range(1, 13))
        ])
        await db.commit()

    response = await client.get(
        PREFIX + "/interaction-analytics",
        params={"start_date": "2026-01-01", "end_date": "2026-12-31"},
    )

    assert response.status_code == 200, response.text
    trend = response.json()["trend"]
    assert 1 <= len(trend) <= 15
    assert sum(item["interactions"] for item in trend) == 12


@pytest.mark.asyncio
async def test_interaction_analytics_exports_filtered_workbook(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = admin
    owner_id = "analytics-export-owner"
    async with factory() as db:
        conversation = Conversation(
            id="analytics-export-conversation", owner_id=owner_id, title="导出测试"
        )
        db.add(conversation)
        db.add_all([
            Message(id="analytics-export-user", conversation_id=conversation.id, sequence_no=1, role="user", content="导出的问题", created_at=datetime(2026, 11, 13, 2, 3, 4, 567000, tzinfo=UTC)),
            Message(id="analytics-export-assistant", conversation_id=conversation.id, sequence_no=2, role="assistant", content="导出的回答", created_at=datetime(2026, 11, 13, 2, 3, 5, tzinfo=UTC)),
        ])
        await db.commit()

    reference = dependencies.owner_reference(owner_id)
    formatted = "-".join(reference.upper()[index:index + 4] for index in range(0, 16, 4))
    response = await client.get(
        PREFIX + "/interaction-analytics/export",
        params={"start_date": "2026-11-13", "end_date": "2026-11-13", "user_id": formatted},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    workbook = load_workbook(io.BytesIO(response.content))
    sheet = workbook.active
    assert sheet is not None
    assert [sheet.cell(1, column).value for column in range(1, 6)] == [
        "分类", "标准问题", "其他问法", "答案内容", "有效日期"
    ]
    assert sheet["A2"].value == "用户交互"
    assert sheet["B2"].value == "导出的问题"
    assert sheet["D2"].value == "导出的回答"
    assert sheet["E2"].value == "2088-01-01 10:03:567"
    workbook.close()
    assert (
        await client.get(
            PREFIX + "/interaction-analytics",
            params={"start_date": "2026-11-12", "end_date": "2026-11-11"},
        )
    ).status_code == 422


@pytest.mark.asyncio
async def test_multi_turn_conversation_test_uses_real_flow_and_cleans_up(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, factory = admin

    class StreamOnlyProvider:
        async def complete(self, _: list[ChatTurn]) -> str:
            raise AssertionError("admin test must use the patient streaming path")

        async def stream(self, _: list[ChatTurn]) -> AsyncIterator[str]:
            yield "患者端"
            yield "流式回答。"

    def build_stream_only_provider(_: Settings) -> StreamOnlyProvider:
        return StreamOnlyProvider()

    monkeypatch.setattr(
        conversation_service,
        "build_chat_provider",
        build_stream_only_provider,
    )
    stream_settings = conversation_service.get_settings().model_copy(
        update={"rag_enabled": False}
    )
    monkeypatch.setattr(conversation_service, "get_settings", lambda: stream_settings)
    before = (await client.get(PREFIX + "/overview")).json()["interaction_count"]
    response = await client.post(
        PREFIX + "/conversation-tests",
        json={"turns": [{"content": "第一轮问题"}, {"content": "基于上一轮继续追问"}]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["turn_count"] == 2
    assert [item["turn"] for item in payload["turns"]] == [1, 2]
    assert [item["output"] for item in payload["turns"]] == [
        "患者端流式回答。",
        "患者端流式回答。",
    ]
    assert all("duration_ms" in item for item in payload["turns"])
    assert all("knowledge_hits" in item for item in payload["turns"])

    async with factory() as db:
        temporary = await db.scalar(
            select(Conversation).where(Conversation.owner_id.like("admin-test-%"))
        )
        assert temporary is None
    after = (await client.get(PREFIX + "/overview")).json()["interaction_count"]
    assert after == before


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
    preview_url = f"{PREFIX}/releases/{release_id}/import-preview"
    apply_url = f"{PREFIX}/releases/{release_id}/import-apply"
    preview = await client.post(preview_url, files={"file": ("knowledge.xlsx", data)})
    assert preview.status_code == 200, preview.text
    assert preview.json()["counts"] == {
        "added": 1, "updated": 1, "unchanged": 0, "conflict": 0
    }
    first = await client.post(
        apply_url,
        files={"file": ("knowledge.xlsx", data)},
        data={
            "selected_rows": "[2, 3]",
            "checksum": preview.json()["checksum"],
            "draft_checksum": preview.json()["draft_checksum"],
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["added"] == 1 and first.json()["updated"] == 1
    second = await client.post(preview_url, files={"file": ("knowledge.xlsx", data)})
    assert second.json()["counts"]["unchanged"] == 2
    rows = (await client.get(f"{PREFIX}/releases/{release_id}/entries")).json()["items"]
    assert len(rows) == 3
    assert next(r for r in rows if r["question"] == "旧文件保留条目")["vectorized"]
    conflict = workbook([["a", "同一问题", "答案一", ""], ["a", "同一问题", "答案二", ""]])
    comparison = await client.post(
        preview_url, files={"file": ("knowledge.xlsx", conflict)}
    )
    assert comparison.status_code == 200
    assert comparison.json()["counts"]["conflict"] == 2
    chosen = await client.post(
        apply_url,
        files={"file": ("knowledge.xlsx", conflict)},
        data={
            "selected_rows": "[2]",
            "checksum": comparison.json()["checksum"],
            "draft_checksum": comparison.json()["draft_checksum"],
        },
    )
    assert chosen.status_code == 200 and chosen.json()["added"] == 1
    assert (
        await client.post(preview_url, files={"file": ("knowledge.xlsx", b"not-xlsx")})
    ).status_code == 422


@pytest.mark.asyncio
async def test_entry_search_uses_alias_tokens_and_category_filter(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = admin
    release_id = await draft(client)
    await add(client, release_id, question="术前需要准备什么", aliases="手术 前一天 准备", category="术前")
    await add(client, release_id, question="术后如何清洁", aliases="清洁护理", category="术后")

    response = await client.get(
        f"{PREFIX}/releases/{release_id}/entries",
        params={"q": "手术 准备", "category": "术前"},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["total"] == 1
    assert payload["items"][0]["question"] == "术前需要准备什么"
    assert payload["categories"] == ["术前", "术后"]


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
