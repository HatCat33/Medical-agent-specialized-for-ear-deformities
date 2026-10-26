import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from medical_agent.api import dependencies as api_dependencies
from medical_agent.api.router import api_router
from medical_agent.core.config import Settings
from medical_agent.core.database import Base, get_db
from medical_agent.modules.conversation import repository as conversation_repository
from medical_agent.modules.conversation import service as conversation_service
from medical_agent.modules.conversation.models import Conversation, Message
from medical_agent.modules.knowledge import router as knowledge_router_module
from medical_agent.modules.knowledge import service as knowledge_service
from medical_agent.modules.knowledge.models import KnowledgeEntry, KnowledgeRelease
from medical_agent.modules.media import router as media_router_module
from medical_agent.modules.speech import router as speech_router_module
from medical_agent.modules.speech import service as speech_service
from medical_agent.modules.system.models import PromptRelease
from medical_agent.providers.llm.client import DemoChatProvider, ProviderError
from medical_agent.providers.llm.protocol import ChatTurn
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
    settings = Settings(media_storage_path=str(tmp_path / "media"), rag_enabled=False)
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

    isolated = await client.get(f"/api/v1/conversations/{conversation_id}", headers=other_headers)
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
    assert "Demo 测试模式" in payload["assistant_message"]["content"]
    assert payload["conversation"]["title"] == "第一次就诊前需要准备什么？"

    history = await client.get(f"/api/v1/conversations/{conversation_id}", headers=owner_headers)
    assert history.status_code == 200
    assert [message["role"] for message in history.json()["messages"]] == [
        "user",
        "assistant",
    ]

    conversations = await client.get("/api/v1/conversations", headers=owner_headers)
    assert conversations.status_code == 200
    assert [item["id"] for item in conversations.json()] == [conversation_id]

    deleted = await client.delete(f"/api/v1/conversations/{conversation_id}", headers=owner_headers)
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

    history = await client.get(f"/api/v1/conversations/{conversation_id}", headers=headers)
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
    assert [item["id"] for item in user_message["attachments"]] == [item["id"] for item in media]
    assert all(item["status"] == "attached" for item in user_message["attachments"])

    history = await client.get(f"/api/v1/conversations/{conversation_id}", headers=owner_headers)
    assert [item["id"] for item in history.json()["messages"][0]["attachments"]] == [
        item["id"] for item in media
    ]
    assert (
        await client.delete(f"/api/v1/media/{media_id}", headers=owner_headers)
    ).status_code == 409

    deleted = await client.delete(f"/api/v1/conversations/{conversation_id}", headers=owner_headers)
    assert deleted.status_code == 204
    assert (
        await client.get(f"/api/v1/media/{media_id}/content", headers=owner_headers)
    ).status_code == 404


@pytest.mark.asyncio
async def test_followup_reuses_images_only_inside_the_same_conversation(
    api_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = api_client
    calls: list[list[ChatTurn]] = []

    class ImageContextProvider:
        async def complete(self, messages: list[ChatTurn]) -> str:
            calls.append(messages)
            return "已结合当前会话中的图片上下文回答。"

        async def stream(self, messages: list[ChatTurn]) -> AsyncIterator[str]:
            yield await self.complete(messages)

    def build_image_context_provider(_: Settings) -> ImageContextProvider:
        return ImageContextProvider()

    monkeypatch.setattr(
        conversation_service,
        "build_chat_provider",
        build_image_context_provider,
    )
    headers = {"X-Visitor-ID": "visitor-image-context-013"}
    png = b"\x89PNG\r\n\x1a\n" + b"same-conversation-image"
    uploaded = await client.post(
        "/api/v1/media/images",
        files=[("images", ("first.png", png, "image/png"))],
        headers=headers,
    )
    media_id = uploaded.json()[0]["id"]
    created = await client.post("/api/v1/conversations", json={}, headers=headers)
    conversation_id = created.json()["id"]

    first = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={
            "content": "这是我刚才提到的第一张照片。",
            "attachment_ids": [media_id],
            "input_mode": "multimodal",
        },
        headers=headers,
    )
    assert first.status_code == 200
    followup = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "这张图还有哪些可客观说明的信息？"},
        headers=headers,
    )
    assert followup.status_code == 200

    assert len(calls) == 2
    historical_turn = next(
        turn for turn in calls[1] if "第一张照片" in turn.content
    )
    assert len(historical_turn.images) == 1
    assert historical_turn.images[0].data == png
    assert "会话图片上下文" in historical_turn.content
    assert not next(turn for turn in reversed(calls[1]) if turn.role == "user").images

    other = await client.post("/api/v1/conversations", json={}, headers=headers)
    isolated_followup = await client.post(
        f"/api/v1/conversations/{other.json()['id']}/messages",
        json={"content": "刚才那张图还有什么信息？"},
        headers=headers,
    )
    assert isolated_followup.status_code == 200
    assert not any(turn.images for turn in calls[2])


@pytest.mark.asyncio
async def test_long_conversation_rolls_old_messages_into_summary_and_keeps_old_image(
    api_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_maker = api_client
    answer_calls: list[list[ChatTurn]] = []
    summary_calls: list[list[ChatTurn]] = []

    class MemoryProvider:
        async def complete(self, messages: list[ChatTurn]) -> str:
            if messages[0].content.startswith("你负责压缩"):
                summary_calls.append(messages)
                return "患者已更正：患儿目前4岁；仍关注首次就诊准备。"
            answer_calls.append(messages)
            return "已结合本次会话记忆和历史图片回答。"

        async def stream(self, messages: list[ChatTurn]) -> AsyncIterator[str]:
            yield await self.complete(messages)

    provider = MemoryProvider()

    def build_memory_provider(_: Settings) -> MemoryProvider:
        return provider

    monkeypatch.setattr(conversation_service, "build_chat_provider", build_memory_provider)
    settings = conversation_service.get_settings().model_copy(
        update={
            "history_message_limit": 20,
            "history_summary_batch_size": 8,
            "history_summary_max_chars": 3000,
        }
    )
    monkeypatch.setattr(conversation_service, "get_settings", lambda: settings)

    headers = {"X-Visitor-ID": "visitor-long-memory-014"}
    png = b"\x89PNG\r\n\x1a\n" + b"old-conversation-image"
    uploaded = await client.post(
        "/api/v1/media/images",
        files=[("images", ("old.png", png, "image/png"))],
        headers=headers,
    )
    created = await client.post("/api/v1/conversations", json={}, headers=headers)
    conversation_id = created.json()["id"]
    first = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={
            "content": "这是患儿3岁时拍的耳部照片。",
            "attachment_ids": [uploaded.json()[0]["id"]],
            "input_mode": "multimodal",
        },
        headers=headers,
    )
    assert first.status_code == 200

    async with session_maker() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        for index in range(13):
            user_content = (
                "更正前面的年龄信息，患儿目前4岁。"
                if index == 3
                else f"第{index + 2}轮患者补充"
            )
            await conversation_repository.add_message(
                session,
                conversation=conversation,
                role="user",
                content=user_content,
            )
            await conversation_repository.add_message(
                session,
                conversation=conversation,
                role="assistant",
                content=f"第{index + 2}轮助手回复",
            )
        await session.commit()

    followup = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "结合之前那张照片和我更正的信息，还要准备什么？"},
        headers=headers,
    )
    assert followup.status_code == 200
    assert len(summary_calls) == 1
    assert "患儿目前4岁" in summary_calls[0][1].content
    assert not any(turn.images for turn in summary_calls[0])

    final_call = answer_calls[-1]
    assert "<当前会话压缩记忆>" in final_call[0].content
    assert "患儿目前4岁" in final_call[0].content
    old_image_turn = next(turn for turn in final_call if "3岁时拍的耳部照片" in turn.content)
    assert len(old_image_turn.images) == 1
    assert old_image_turn.images[0].data == png

    async with session_maker() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.memory_summary.startswith("患者已更正")
        assert conversation.memory_through_sequence == 9
        assert conversation.memory_updated_at is not None

    isolated = await client.post("/api/v1/conversations", json={}, headers=headers)
    isolated_response = await client.post(
        f"/api/v1/conversations/{isolated.json()['id']}/messages",
        json={"content": "之前更正的信息是什么？"},
        headers=headers,
    )
    assert isolated_response.status_code == 200
    isolated_call = answer_calls[-1]
    assert "<当前会话压缩记忆>" not in isolated_call[0].content
    assert not any(turn.images for turn in isolated_call)


@pytest.mark.asyncio
async def test_summary_failure_keeps_unsummarized_messages_in_model_context(
    api_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_maker = api_client
    answer_calls: list[list[ChatTurn]] = []

    class FailingSummaryProvider:
        async def complete(self, messages: list[ChatTurn]) -> str:
            if messages[0].content.startswith("你负责压缩"):
                raise ProviderError("summary_provider_unavailable")
            answer_calls.append(messages)
            return "摘要失败后仍正常回答。"

        async def stream(self, messages: list[ChatTurn]) -> AsyncIterator[str]:
            yield await self.complete(messages)

    def build_failing_summary_provider(_: Settings) -> FailingSummaryProvider:
        return FailingSummaryProvider()

    monkeypatch.setattr(
        conversation_service,
        "build_chat_provider",
        build_failing_summary_provider,
    )
    settings = conversation_service.get_settings().model_copy(
        update={"history_message_limit": 4, "history_summary_batch_size": 2}
    )
    monkeypatch.setattr(conversation_service, "get_settings", lambda: settings)

    headers = {"X-Visitor-ID": "visitor-summary-failure-015"}
    created = await client.post("/api/v1/conversations", json={}, headers=headers)
    conversation_id = created.json()["id"]
    async with session_maker() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        for sequence in range(1, 7):
            await conversation_repository.add_message(
                session,
                conversation=conversation,
                role="user" if sequence % 2 else "assistant",
                content=f"必须保留的旧消息{sequence}",
            )
        await session.commit()

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "继续咨询"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["assistant_message"]["content"] == "摘要失败后仍正常回答。"
    model_text = "\n".join(turn.content for turn in answer_calls[-1])
    assert "必须保留的旧消息1" in model_text
    assert "必须保留的旧消息6" in model_text

    async with session_maker() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.memory_summary == ""
        assert conversation.memory_through_sequence == 0


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


@pytest.mark.asyncio
async def test_sync_and_stream_interactions_emit_debug_records(
    api_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _ = api_client
    records: list[tuple[str, dict[str, object]]] = []

    class CapturingInteractionLogger:
        def info(self, event: str, **fields: object) -> None:
            records.append((event, fields))

    settings = Settings(
        media_storage_path=str(tmp_path / "interaction-media"),
        llm_provider="demo",
        rag_enabled=False,
        interaction_log_enabled=True,
        interaction_log_include_content=True,
    )
    monkeypatch.setattr(conversation_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        conversation_service,
        "interaction_logger",
        CapturingInteractionLogger(),
    )

    headers = {"X-Visitor-ID": "visitor-interaction-log-010"}
    sync_conversation = await client.post("/api/v1/conversations", json={}, headers=headers)
    sync_response = await client.post(
        f"/api/v1/conversations/{sync_conversation.json()['id']}/messages",
        json={"content": "普通请求日志测试"},
        headers=headers,
    )
    assert sync_response.status_code == 200

    stream_conversation = await client.post("/api/v1/conversations", json={}, headers=headers)
    stream_response = await client.post(
        f"/api/v1/conversations/{stream_conversation.json()['id']}/messages/stream",
        json={"content": "流式请求日志测试"},
        headers=headers,
    )
    assert stream_response.status_code == 200

    assert [event for event, _ in records] == [
        "conversation_interaction",
        "conversation_interaction",
    ]
    assert [record["transport"] for _, record in records] == ["request_response", "sse"]
    assert [record["input_text"] for _, record in records] == [
        "普通请求日志测试",
        "流式请求日志测试",
    ]
    assert all("Demo 测试模式" in str(record["output_text"]) for _, record in records)
    assert all(record["status"] == "completed" for _, record in records)
    assert all(record["provider"] == "demo" for _, record in records)
    assert all(record["owner_ref"] != headers["X-Visitor-ID"] for _, record in records)
    assert all("input_sha256" in record and "output_sha256" in record for _, record in records)


@pytest.mark.asyncio
async def test_published_knowledge_is_injected_and_cited(
    api_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, session_maker = api_client
    release_id = str(uuid.uuid4())
    async with session_maker() as session:
        session.add(
            KnowledgeRelease(
                id=release_id,
                name="医院测试知识",
                source_name="private-source.xlsx",
                source_checksum="b" * 64,
                status="published",
                entry_count=1,
                published_at=datetime.now(UTC),
            )
        )
        session.add(
            KnowledgeEntry(
                id=str(uuid.uuid4()),
                release_id=release_id,
                category="就诊准备",
                question="第一次到医院要准备什么？",
                aliases="初诊资料",
                answer="携带已有检查资料并整理需要咨询的问题。",
                effective_date="",
                source_sheet="FAQ",
                source_row=2,
                search_text="第一次到医院要准备什么 初诊资料 携带已有检查资料",
            )
        )
        await session.commit()

    captured: list[ChatTurn] = []

    class KnowledgeAwareProvider:
        async def complete(self, messages: list[ChatTurn]) -> str:
            captured.extend(messages)
            return "请携带已有检查资料。[1]"

        async def stream(self, messages: list[ChatTurn]) -> AsyncIterator[str]:
            yield await self.complete(messages)

    def build_knowledge_provider(_: Settings) -> KnowledgeAwareProvider:
        return KnowledgeAwareProvider()

    settings = Settings(
        media_storage_path=str(tmp_path / "rag-media"),
        rag_enabled=True,
        rag_min_score=0.1,
    )
    monkeypatch.setattr(conversation_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        conversation_service,
        "build_chat_provider",
        build_knowledge_provider,
    )

    headers = {"X-Visitor-ID": "visitor-rag-010"}
    created = await client.post("/api/v1/conversations", json={}, headers=headers)
    response = await client.post(
        f"/api/v1/conversations/{created.json()['id']}/messages",
        json={"content": "第一次就诊要带哪些资料？"},
        headers=headers,
    )

    assert response.status_code == 200
    assert "医院测试知识" in response.json()["assistant_message"]["content"]
    assert captured[0].role == "system"
    assert "携带已有检查资料" in captured[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_published_system_and_business_prompt_are_used(
    api_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
    streaming: bool,
) -> None:
    client, session_maker = api_client
    async with session_maker() as session:
        session.add(
            PromptRelease(
                id=str(uuid.uuid4()),
                name="患者表达测试",
                content="每次回答开头先使用测试短语。",
                system_content="自定义系统规则：不进行诊断。",
                checksum="c" * 64,
                status="published",
                published_at=datetime.now(UTC),
            )
        )
        await session.commit()

    captured: list[ChatTurn] = []

    class PromptAwareProvider:
        async def complete(self, messages: list[ChatTurn]) -> str:
            captured.extend(messages)
            return "测试回答"

        async def stream(self, messages: list[ChatTurn]) -> AsyncIterator[str]:
            yield await self.complete(messages)

    def build_prompt_provider(_: Settings) -> PromptAwareProvider:
        return PromptAwareProvider()

    monkeypatch.setattr(
        conversation_service,
        "build_chat_provider",
        build_prompt_provider,
    )
    headers = {"X-Visitor-ID": "visitor-prompt-011"}
    created = await client.post("/api/v1/conversations", json={}, headers=headers)
    response = await client.post(
        f"/api/v1/conversations/{created.json()['id']}/messages" + ("/stream" if streaming else ""),
        json={"content": "提示词是否生效？"},
        headers=headers,
    )

    assert response.status_code == 200
    assert "每次回答开头先使用测试短语" in captured[0].content
    assert "不进行诊断" in captured[0].content
    assert "自定义系统规则" in captured[0].content
    assert "以下医疗安全约束始终生效" not in captured[0].content


@pytest.mark.asyncio
async def test_admin_can_inspect_and_backfill_knowledge_embeddings(
    api_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_maker = api_client
    release_id = str(uuid.uuid4())
    async with session_maker() as session:
        session.add(
            KnowledgeRelease(
                id=release_id,
                name="管理端向量测试",
                source_name="FAQ.xlsx",
                source_checksum="d" * 64,
                status="published",
                entry_count=1,
                published_at=datetime.now(UTC),
            )
        )
        session.add(
            KnowledgeEntry(
                id=str(uuid.uuid4()),
                release_id=release_id,
                category="护理",
                question="如何进行日常护理？",
                aliases="护理事项",
                answer="请遵循医生指导。",
                effective_date="",
                source_sheet="FAQ",
                source_row=2,
                search_text="如何进行日常护理 护理事项 请遵循医生指导",
            )
        )
        await session.commit()

    settings = Settings(
        admin_api_token="test-admin-token",
        embedding_base_url="https://embedding.example/v1",
        embedding_api_key="test-key",
        embedding_model="test-embedding",
        rag_enabled=True,
    )
    monkeypatch.setattr(api_dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(knowledge_router_module, "get_settings", lambda: settings)

    class FakeEmbeddingProvider:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.25, 0.75] for _ in texts]

    def build_fake_embedding_provider(_: Settings) -> FakeEmbeddingProvider:
        return FakeEmbeddingProvider()

    monkeypatch.setattr(
        knowledge_service,
        "build_embedding_provider",
        build_fake_embedding_provider,
    )

    unauthorized = await client.get("/api/v1/admin/knowledge/releases/current")
    assert unauthorized.status_code == 401

    headers = {"X-Admin-Token": "test-admin-token"}
    before = await client.get(
        "/api/v1/admin/knowledge/releases/current",
        headers=headers,
    )
    assert before.status_code == 200
    assert before.json()["pending_count"] == 1
    assert before.json()["embedding_configured"] is True

    rebuilt = await client.post(
        f"/api/v1/admin/knowledge/releases/{release_id}/embeddings/rebuild",
        json={"batch_size": 1, "rebuild_all": False},
        headers=headers,
    )
    assert rebuilt.status_code == 200
    assert rebuilt.json()["processed_entries"] == 1
    assert rebuilt.json()["vectorized_count"] == 1

    after = await client.get(
        "/api/v1/admin/knowledge/releases/current",
        headers=headers,
    )
    assert after.json()["pending_count"] == 0
