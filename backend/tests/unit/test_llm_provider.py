import pytest

from medical_agent.providers.llm.client import (
    DemoChatProvider,
    OpenAICompatibleProvider,
    ProviderError,
    ResilientChatProvider,
)
from medical_agent.providers.llm.protocol import ChatTurn, ImageInput


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
