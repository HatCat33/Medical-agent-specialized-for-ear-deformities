import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from medical_agent.api.router import api_router
from medical_agent.core.config import Settings
from medical_agent.core.database import Base, get_db
from medical_agent.modules.conversation import service as conversation_service
from medical_agent.modules.conversation.models import Message
from medical_agent.providers.llm.client import DemoChatProvider


def build_demo_provider(_: Settings) -> DemoChatProvider:
    return DemoChatProvider()


@pytest.fixture
async def api_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession]]]:
    monkeypatch.setattr(
        conversation_service,
        "build_chat_provider",
        build_demo_provider,
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session_maker

    await engine.dispose()


@pytest.mark.asyncio
async def test_conversation_lifecycle_and_owner_isolation(
    api_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_maker = api_client
    owner_headers = {"X-Visitor-ID": "visitor-owner-001"}
    other_headers = {"X-Visitor-ID": "visitor-other-002"}

    created = await client.post("/api/v1/conversations", json={}, headers=owner_headers)
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    isolated = await client.get(
        f"/api/v1/conversations/{conversation_id}", headers=other_headers
    )
    assert isolated.status_code == 404

    sent = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "第一次就诊前需要准备什么？"},
        headers=owner_headers,
    )
    assert sent.status_code == 200
    payload = sent.json()
    assert payload["user_message"]["sequence_no"] == 1
    assert payload["assistant_message"]["sequence_no"] == 2
    assert payload["assistant_message"]["safety_code"] == "allowed"
    assert "第一版演示模式" in payload["assistant_message"]["content"]
    assert payload["conversation"]["title"] == "第一次就诊前需要准备什么？"

    history = await client.get(
        f"/api/v1/conversations/{conversation_id}", headers=owner_headers
    )
    assert history.status_code == 200
    assert [message["role"] for message in history.json()["messages"]] == [
        "user",
        "assistant",
    ]

    conversations = await client.get("/api/v1/conversations", headers=owner_headers)
    assert conversations.status_code == 200
    assert [item["id"] for item in conversations.json()] == [conversation_id]

    deleted = await client.delete(
        f"/api/v1/conversations/{conversation_id}", headers=owner_headers
    )
    assert deleted.status_code == 204
    assert (
        await client.get(f"/api/v1/conversations/{conversation_id}", headers=owner_headers)
    ).status_code == 404

    async with session_maker() as session:
        remaining_messages = await session.scalar(select(func.count()).select_from(Message))
    assert remaining_messages == 0


@pytest.mark.asyncio
async def test_medical_boundary_response_is_saved(
    api_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = api_client
    headers = {"X-Visitor-ID": "visitor-boundary-003"}
    created = await client.post("/api/v1/conversations", json={}, headers=headers)
    conversation_id = created.json()["id"]

    sent = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "请帮我诊断属于哪种畸形类型"},
        headers=headers,
    )

    assert sent.status_code == 200
    assistant = sent.json()["assistant_message"]
    assert assistant["safety_code"] == "medical_boundary"
    assert "不能进行诊断" in assistant["content"]


@pytest.mark.asyncio
async def test_streaming_boundary_response_is_emitted_and_saved(
    api_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = api_client
    headers = {"X-Visitor-ID": "visitor-stream-004"}
    created = await client.post("/api/v1/conversations", json={}, headers=headers)
    conversation_id = created.json()["id"]

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "请帮我诊断属于哪种畸形类型"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events: list[tuple[str, dict[str, object]]] = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        event = next(line.removeprefix("event: ") for line in lines if line.startswith("event:"))
        data_line = next(line.removeprefix("data: ") for line in lines if line.startswith("data:"))
        events.append((event, json.loads(data_line)))

    assert [event for event, _ in events] == ["user_message", "delta", "done"]
    assert "不能进行诊断" in str(events[1][1]["content"])

    history = await client.get(
        f"/api/v1/conversations/{conversation_id}", headers=headers
    )
    assert history.status_code == 200
    messages = history.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["safety_code"] == "medical_boundary"
