import pytest

from medical_agent.providers.llm.client import DemoChatProvider, OpenAICompatibleProvider
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
