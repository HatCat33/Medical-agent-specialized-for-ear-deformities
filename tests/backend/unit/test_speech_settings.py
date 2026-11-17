import asyncio
from collections.abc import AsyncGenerator
from typing import cast

import pytest
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.core.config import Settings
from medical_agent.modules.speech import router as speech_router
from medical_agent.modules.system import speech_service
from medical_agent.modules.system.schemas import SpeechSettings, SynthesisRequest


@pytest.mark.asyncio
async def test_speech_settings_snapshot_collapses_concurrent_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Database:
        bind = object()

    database = cast(AsyncSession, cast(object, Database()))
    calls = 0

    async def read_settings(_: AsyncSession) -> SpeechSettings:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return SpeechSettings(rate=1.1)

    speech_service.clear_settings_cache()
    monkeypatch.setattr(speech_service, "read_settings", read_settings)
    values = await asyncio.gather(
        *(speech_service.read_settings_cached(database) for _ in range(25))
    )

    assert calls == 1
    assert all(value.rate == 1.1 for value in values)


@pytest.mark.asyncio
async def test_streaming_tts_closes_database_before_cloud_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Database:
        bind = object()
        closed = False

        async def close(self) -> None:
            self.closed = True

    class Stream:
        closed = False

        async def iter_bytes(self) -> AsyncGenerator[bytes, None]:
            yield b"\x00\x01"

        async def close(self) -> None:
            self.closed = True

    database_value = Database()
    database = cast(AsyncSession, cast(object, database_value))
    stream = Stream()

    async def read_settings(_: AsyncSession) -> SpeechSettings:
        return SpeechSettings(provider="openai-compatible", cloud_voice="voice")

    async def open_stream(
        text: str,
        preferences: SpeechSettings,
        settings: Settings,
    ) -> Stream:
        assert text == "测试播报"
        assert preferences.cloud_voice == "voice"
        assert database_value.closed is True
        del settings
        return stream

    monkeypatch.setattr(speech_router, "read_settings_cached", read_settings)
    monkeypatch.setattr(speech_router, "open_synthesis_stream", open_stream)
    monkeypatch.setattr(
        speech_router,
        "get_settings",
        lambda: Settings.model_construct(
            tts_stream_sample_rate=24_000,
            tts_stream_channels=1,
        ),
    )

    response = await speech_router.create_streaming_synthesis(
        SynthesisRequest(text="测试播报"),
        owner_id="owner",
        db=database,
        x_visitor_id="visitor",
    )

    assert isinstance(response, StreamingResponse)
    assert database_value.closed is True
    assert response.background is not None
    await response.background()
    assert stream.closed is True
