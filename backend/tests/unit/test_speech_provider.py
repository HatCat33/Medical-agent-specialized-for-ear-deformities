import httpx
import pytest

from medical_agent.core.config import Settings
from medical_agent.providers.speech.client import OpenAICompatibleSpeechProvider
from medical_agent.providers.speech.protocol import AudioInput


@pytest.mark.asyncio
async def test_openai_compatible_speech_provider_sends_multipart_audio() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://speech.example/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer secret"
        assert "multipart/form-data" in request.headers["content-type"]
        body = request.content
        assert b'name="model"' in body
        assert b"example-asr" in body
        assert b'name="language"' in body
        assert b"recording.webm" in body
        assert b"audio-content" in body
        return httpx.Response(200, json={"text": "第一次就诊需要准备什么"})

    provider = OpenAICompatibleSpeechProvider(
        Settings(
            asr_provider="openai-compatible",
            asr_base_url="https://speech.example/v1",
            asr_api_key="secret",
            asr_model="example-asr",
            asr_language="zh",
        ),
        transport=httpx.MockTransport(handler),
    )

    result = await provider.transcribe(
        AudioInput(
            filename="recording.webm",
            media_type="audio/webm",
            data=b"audio-content",
        )
    )

    assert result == "第一次就诊需要准备什么"
