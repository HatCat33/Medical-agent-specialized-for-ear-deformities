import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from medical_agent.core.config import Settings
from medical_agent.providers.llm import client as llm_client
from medical_agent.providers.llm.client import (
    DemoChatProvider,
    OpenAICompatibleProvider,
    ProviderError,
    ResilientChatProvider,
)
from medical_agent.providers.llm.protocol import ChatTurn, ImageInput


@pytest.fixture(autouse=True)
async def clean_clients() -> AsyncIterator[None]:
    await llm_client.close_chat_clients()
    yield
    await llm_client.close_chat_clients()


@pytest.mark.asyncio
async def test_demo_provider_stream_matches_complete_response() -> None:
    provider = DemoChatProvider()
    messages = [ChatTurn(role="user", content="测试问题")]

    chunks = [chunk async for chunk in provider.stream(messages)]
    completed = await provider.complete(messages)

    assert len(chunks) > 1
    assert "测试问题" in completed
    assert "".join(chunks) == completed


def test_openai_provider_serializes_multimodal_turn() -> None:
    turn = ChatTurn(
        role="user",
        content="请客观描述图片",
        images=(ImageInput(media_type="image/png", data=b"image"),),
    )

    serialized = OpenAICompatibleProvider.serialize_turn(turn)

    assert serialized["role"] == "user"
    content = serialized["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "请客观描述图片"}
    assert content[1] == {
        "type": "image_url",
        "image_url": {
            "url": "data:image/png;base64,aW1hZ2U=",
            "detail": "auto",
        },
    }


@pytest.mark.asyncio
async def test_openai_provider_reuses_client_and_parses_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = 0
    original = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            headers={"x-request-id": "provider-trace"},
            json={"error": {"code": "concurrency_exceeded", "message": "capacity full"}},
        )

    def client(*, timeout: httpx.Timeout, limits: httpx.Limits) -> httpx.AsyncClient:
        nonlocal created
        created += 1
        del limits
        return original(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", client)
    settings = Settings(
        llm_provider="openai-compatible",
        llm_base_url="https://chat.example/v1",
        llm_api_key="secret",
        llm_model="chat-model",
    )
    provider = OpenAICompatibleProvider(settings)
    for _ in range(2):
        with pytest.raises(ProviderError) as captured:
            await provider.complete([ChatTurn(role="user", content="测试")])
        assert captured.value.status_code == 400
        assert captured.value.upstream_code == "concurrency_exceeded"
        assert captured.value.upstream_message == "capacity full"
        assert captured.value.request_id == "provider-trace"
    assert created == 1


@pytest.mark.asyncio
async def test_openai_provider_timeout_closes_stream_without_async_generator_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            await asyncio.sleep(10)
            yield b'data: {"choices":[{"delta":{"content":"late"}}]}\n\n'

    original = httpx.AsyncClient

    def client(*, timeout: httpx.Timeout, limits: httpx.Limits) -> httpx.AsyncClient:
        del limits
        return original(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    stream=SlowStream(),
                )
            ),
            timeout=timeout,
        )

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", client)
    provider = OpenAICompatibleProvider(
        Settings(
            llm_provider="openai-compatible",
            llm_base_url="https://chat.example/v1",
            llm_api_key="secret",
            llm_model="chat-model",
            llm_timeout_seconds=0.02,
        )
    )
    with pytest.raises(ProviderError) as captured:
        await provider.complete([ChatTurn(role="user", content="测试")])
    assert str(captured.value) == "model_request_timeout"


@pytest.mark.asyncio
async def test_resilient_provider_retries_then_uses_fallback_before_first_token() -> None:
    class FailingProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, messages: list[ChatTurn]) -> str:
            return ""

        async def stream(self, messages: list[ChatTurn]):  # type: ignore[no-untyped-def]
            self.calls += 1
            if False:
                yield ""
            raise ProviderError("model_request_failed")

    class FallbackProvider:
        async def complete(self, messages: list[ChatTurn]) -> str:
            return "备用回答"

        async def stream(self, messages: list[ChatTurn]):  # type: ignore[no-untyped-def]
            yield "备用回答"

    primary = FailingProvider()
    provider = ResilientChatProvider(primary, retry_count=1, fallback=FallbackProvider())

    assert await provider.complete([ChatTurn(role="user", content="测试")]) == "备用回答"
    assert primary.calls == 2


@pytest.mark.asyncio
async def test_resilient_provider_does_not_retry_after_partial_output() -> None:
    class PartialProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, messages: list[ChatTurn]) -> str:
            return ""

        async def stream(self, messages: list[ChatTurn]):  # type: ignore[no-untyped-def]
            self.calls += 1
            yield "已经输出"
            raise ProviderError("model_request_failed")

    primary = PartialProvider()
    provider = ResilientChatProvider(primary, retry_count=1)
    chunks: list[str] = []
    with pytest.raises(ProviderError):
        async for chunk in provider.stream([ChatTurn(role="user", content="测试")]):
            chunks.append(chunk)

    assert chunks == ["已经输出"]
    assert primary.calls == 1
