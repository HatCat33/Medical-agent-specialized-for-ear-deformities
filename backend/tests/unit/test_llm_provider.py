import pytest

from medical_agent.providers.llm.client import DemoChatProvider
from medical_agent.providers.llm.protocol import ChatTurn


@pytest.mark.asyncio
async def test_demo_provider_stream_matches_complete_response() -> None:
    provider = DemoChatProvider()
    messages = [ChatTurn(role="user", content="测试问题")]

    chunks = [chunk async for chunk in provider.stream(messages)]
    completed = await provider.complete(messages)

    assert len(chunks) > 1
    assert "测试问题" in completed
    assert "".join(chunks) == completed
