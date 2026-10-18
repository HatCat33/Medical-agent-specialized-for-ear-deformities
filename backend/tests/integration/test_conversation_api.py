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
from medical_agent.modules.media import router as media_router_module
from medical_agent.modules.speech import router as speech_router_module
from medical_agent.modules.speech import service as speech_service
from medical_agent.providers.llm.client import DemoChatProvider
from medical_agent.providers.speech.protocol import AudioInput


def build_demo_provider(_: Settings) -> DemoChatProvider:
    return DemoChatProvider()


class StubSpeechProvider:
    async def transcribe(self, audio: AudioInput) -> str:
        assert audio.media_type == "audio/webm"
        assert audio.data.startswith(b"\x1aE\xdf\xa3")
        return "语音转写后的问题"


def build_stub_speech_provider(_: Settings) -> StubSpeechProvider:
    return StubSpeechProvider()


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
    settings = Settings(media_storage_path=str(tmp_path / "media"))
    monkeypatch.setattr(conversation_service, "get_settings", lambda: settings)
    monkeypatch.setattr(media_router_module, "get_settings", lambda: settings)
    monkeypatch.setattr(speech_router_module, "get_settings", lambda: settings)
    monkeypatch.setattr(speech_service, "build_speech_provider", build_stub_speech_provider)
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
    assert "第二版演示模式" in payload["assistant_message"]["content"]
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


@pytest.mark.asyncio
async def test_image_attachments_are_private_persisted_and_deleted_with_history(
    api_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = api_client
    owner_headers = {"X-Visitor-ID": "visitor-media-owner-005"}
    other_headers = {"X-Visitor-ID": "visitor-media-other-006"}
    png = b"\x89PNG\r\n\x1a\n" + b"test-image-content"

    uploaded = await client.post(
        "/api/v1/media/images",
        files=[
            ("images", ("front.png", png, "image/png")),
            ("images", ("side.png", png, "image/png")),
        ],
        headers=owner_headers,
    )
    assert uploaded.status_code == 201
    media = uploaded.json()
    assert len(media) == 2
    assert all(item["status"] == "pending" for item in media)

    media_id = media[0]["id"]
    assert (
        await client.get(f"/api/v1/media/{media_id}/content", headers=other_headers)
    ).status_code == 404
    content = await client.get(f"/api/v1/media/{media_id}/content", headers=owner_headers)
    assert content.status_code == 200
    assert content.content == png

    created = await client.post("/api/v1/conversations", json={}, headers=owner_headers)
    conversation_id = created.json()["id"]
    sent = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={
            "content": "",
            "attachment_ids": [item["id"] for item in media],
            "input_mode": "multimodal",
        },
        headers=owner_headers,
    )
    assert sent.status_code == 200
    user_message = sent.json()["user_message"]
    assert user_message["content"] == "请对上传图片中与耳部健康科普相关的可见信息进行客观说明。"
    assert [item["id"] for item in user_message["attachments"]] == [
        item["id"] for item in media
    ]
    assert all(item["status"] == "attached" for item in user_message["attachments"])

    history = await client.get(
        f"/api/v1/conversations/{conversation_id}", headers=owner_headers
    )
    assert [item["id"] for item in history.json()["messages"][0]["attachments"]] == [
        item["id"] for item in media
    ]
    assert (
        await client.delete(f"/api/v1/media/{media_id}", headers=owner_headers)
    ).status_code == 409

    deleted = await client.delete(
        f"/api/v1/conversations/{conversation_id}", headers=owner_headers
    )
    assert deleted.status_code == 204
    assert (
        await client.get(f"/api/v1/media/{media_id}/content", headers=owner_headers)
    ).status_code == 404


@pytest.mark.asyncio
async def test_media_rejects_unsupported_content(
    api_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = api_client
    response = await client.post(
        "/api/v1/media/images",
        files=[("images", ("fake.png", b"not-an-image", "image/png"))],
        headers={"X-Visitor-ID": "visitor-media-invalid-007"},
    )
    assert response.status_code == 415


@pytest.mark.asyncio
async def test_audio_is_transcribed_without_becoming_persistent_media(
    api_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = api_client
    webm = b"\x1aE\xdf\xa3" + b"ephemeral-audio-content"

    response = await client.post(
        "/api/v1/speech/transcriptions",
        files=[("audio", ("recording.webm", webm, "audio/webm"))],
        headers={"X-Visitor-ID": "visitor-speech-008"},
    )

    assert response.status_code == 200
    assert response.json() == {"text": "语音转写后的问题"}


@pytest.mark.asyncio
async def test_audio_transcription_rejects_unknown_content(
    api_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = api_client
    response = await client.post(
        "/api/v1/speech/transcriptions",
        files=[("audio", ("recording.webm", b"not-audio", "audio/webm"))],
        headers={"X-Visitor-ID": "visitor-speech-invalid-009"},
    )
    assert response.status_code == 415
