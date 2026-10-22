import json

import httpx
import pytest
from fastapi import HTTPException

from medical_agent.core.config import Settings
from medical_agent.modules.speech import synthesis
from medical_agent.modules.system.probe_diagnostics import failure_detail
from medical_agent.modules.system.schemas import SpeechSettings


def test_siliconflow_presets_are_model_qualified_but_custom_ids_are_preserved() -> None:
    settings = Settings.model_construct(
        tts_base_url="https://api.siliconflow.cn/v1",
        tts_model="FunAudioLLM/CosyVoice2-0.5B",
        tts_voice="anna",
    )
    assert synthesis.effective_voice(SpeechSettings(), settings) == f"{settings.tts_model}:anna"
    for custom in ("speech:custom:123", "different-model:anna"):
        assert synthesis.effective_voice(SpeechSettings(cloud_voice=custom), settings) == custom
    other = settings.model_copy(update={"tts_base_url": "https://other.example/v1"})
    assert synthesis.effective_voice(SpeechSettings(), other) == "anna"


@pytest.mark.asyncio
async def test_streamed_tts_error_is_available_to_sanitized_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = httpx.AsyncClient

    def client(*, timeout: float) -> httpx.AsyncClient:
        return original(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    400,
                    headers={"x-request-id": "tts-trace"},
                    json={"code": 20047, "message": "Invalid voice. secret-tts"},
                )
            ),
            timeout=timeout,
        )

    monkeypatch.setattr(synthesis.httpx, "AsyncClient", client)
    settings = Settings.model_construct(
        tts_base_url="https://tts.example/v1",
        tts_api_key="secret-tts",
        tts_model="voice-model",
        tts_voice="bad-voice",
    )
    with pytest.raises(HTTPException) as error:
        await synthesis.synthesize("测试", SpeechSettings(), settings)
    detail = failure_detail(error.value, "tts", settings)
    assert detail["http_status"] == 400
    assert detail["upstream_code"] == "20047"
    assert "Invalid voice" in detail["upstream_message"]
    assert detail["request_id"] == "tts-trace"
    assert "secret-tts" not in str(detail)


@pytest.mark.asyncio
async def test_custom_voice_is_forwarded_without_exposing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = httpx.AsyncClient
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"ID3test-audio")

    def client(*, timeout: float) -> httpx.AsyncClient:
        return original(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(synthesis.httpx, "AsyncClient", client)
    settings = Settings.model_construct(
        tts_base_url="https://voice.test/v1",
        tts_api_key="private-key",
        tts_model="voice-model",
    )
    audio = await synthesis.synthesize(
        "合成测试",
        SpeechSettings(provider="openai-compatible", cloud_voice="custom:voice-id", rate=1.2),
        settings,
    )
    assert audio == b"ID3test-audio"
    request = captured[0]
    assert str(request.url) == "https://voice.test/v1/audio/speech"
    assert request.headers["Authorization"] == "Bearer private-key"
    assert json.loads(request.content) == {
        "model": "voice-model",
        "voice": "custom:voice-id",
        "input": "合成测试",
        "response_format": "mp3",
        "speed": 1.2,
    }


@pytest.mark.asyncio
async def test_synthesis_rejects_missing_configuration_and_non_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(HTTPException) as missing:
        await synthesis.synthesize("test", SpeechSettings(), Settings.model_construct())
    assert missing.value.detail == "tts_not_configured"
    original = httpx.AsyncClient

    def client(*, timeout: float) -> httpx.AsyncClient:
        return original(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"error": "bad"})),
            timeout=timeout,
        )

    monkeypatch.setattr(synthesis.httpx, "AsyncClient", client)
    settings = Settings.model_construct(
        tts_base_url="https://voice.test/v1",
        tts_api_key="private",
        tts_model="voice",
        tts_voice="default",
    )
    with pytest.raises(HTTPException) as invalid:
        await synthesis.synthesize("test", SpeechSettings(), settings)
    assert invalid.value.detail == "tts_response_invalid"
