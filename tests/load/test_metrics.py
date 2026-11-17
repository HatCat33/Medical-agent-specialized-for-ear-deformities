"""Offline checks for partial progress reporting; never call real providers."""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from concurrent_chat import ActiveCounter, PreparedUser, run_request
from mixed_workload import summarize_interactions


@pytest.mark.asyncio
async def test_tts_timeout_preserves_completed_answer_and_partial_audio() -> None:
    closed = asyncio.Event()

    class IncompleteAudio(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"\x01\x00" * 100
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            closed.set()

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages/stream"):
            return httpx.Response(200, text=(
                'event: delta\ndata: {"content":"这是一句完整的测试回答。"}\n\n'
                'event: done\ndata: {"message":{"status":"completed"}}\n\n'
            ))
        assert request.url.path.endswith("/syntheses/stream")
        return httpx.Response(200, stream=IncompleteAudio())

    start = asyncio.Event()
    start.set()
    active = ActiveCounter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result = await run_request(
            client, "http://test/api/v1", PreparedUser(1, "visitor", "conversation"),
            1, "测试问题", start, 0.3, active, "text", None, True, speak_response=True,
        )
    assert not result.success
    assert result.error == "timeout_after_0.3s"
    assert result.answer_done_ms is not None
    assert result.first_audio_ms is not None
    assert result.audio_bytes == 200
    assert result.tts_segments == 1
    assert result.tts_total_ms is None
    assert closed.is_set()
    assert active.current == 0
    summary = summarize_interactions([result])
    assert summary["answers_completed"] == 1
    assert summary["tts_completed"] == 0
    assert summary["first_token"]["samples"] == 1
    assert summary["first_audio"]["samples"] == 1
    assert summary["tts_done"]["samples"] == 0
    assert summary["audio_bytes"] == 200
