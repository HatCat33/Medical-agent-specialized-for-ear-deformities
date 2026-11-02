import asyncio
import base64
import json
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import HTTPException

from medical_agent.core.config import Settings
from medical_agent.modules.speech import synthesis
from medical_agent.modules.system.probe_diagnostics import failure_detail
from medical_agent.modules.system.schemas import SpeechSettings


@pytest.fixture(autouse=True)
async def clean_synthesis_clients() -> AsyncIterator[None]:
    await synthesis.close_synthesis_clients()
    yield
    await synthesis.close_synthesis_clients()


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

    def client(*, timeout: float, **_: object) -> httpx.AsyncClient:
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

    created = 0

    def client(*, timeout: float, **_: object) -> httpx.AsyncClient:
        nonlocal created
        created += 1
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
    await synthesis.synthesize(
        "第二段",
        SpeechSettings(provider="openai-compatible", cloud_voice="custom:voice-id"),
        settings,
    )
    assert created == 1


@pytest.mark.asyncio
async def test_synthesis_rejects_missing_configuration_and_non_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(HTTPException) as missing:
        await synthesis.synthesize("test", SpeechSettings(), Settings.model_construct())
    assert missing.value.detail == "tts_not_configured"
    original = httpx.AsyncClient

    def client(*, timeout: float, **_: object) -> httpx.AsyncClient:
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


@pytest.mark.asyncio
async def test_pcm_synthesis_is_forwarded_without_buffering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = httpx.AsyncClient
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=b"\x01\x00\x02\x00",
        )

    def client(*, timeout: float, **_: object) -> httpx.AsyncClient:
        return original(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(synthesis.httpx, "AsyncClient", client)
    settings = Settings.model_construct(
        tts_base_url="https://voice.test/v1",
        tts_api_key="private",
        tts_model="voice-model",
        tts_voice="voice-id",
        tts_streaming_enabled=True,
    )
    stream = await synthesis.open_synthesis_stream("流式测试", SpeechSettings(), settings)
    blocks = [block async for block in stream.iter_bytes()]
    assert b"".join(blocks) == b"\x01\x00\x02\x00"
    assert json.loads(captured[0].content)["response_format"] == "pcm"


@pytest.mark.asyncio
async def test_azure_and_elevenlabs_protocol_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = httpx.AsyncClient
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"ID3audio")

    def client(*, timeout: float, **_: object) -> httpx.AsyncClient:
        return original(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(synthesis.httpx, "AsyncClient", client)
    azure = Settings.model_construct(
        tts_protocol="azure-speech",
        tts_base_url="https://eastasia.tts.speech.microsoft.com",
        tts_api_key="azure-key",
        tts_voice="zh-CN-XiaoxiaoNeural",
    )
    await synthesis.synthesize("你好", SpeechSettings(), azure)
    assert str(captured[0].url).endswith("/cognitiveservices/v1")
    assert captured[0].headers["ocp-apim-subscription-key"] == "azure-key"
    assert b"zh-CN-XiaoxiaoNeural" in captured[0].content

    await synthesis.close_synthesis_clients()
    eleven = Settings.model_construct(
        tts_protocol="elevenlabs",
        tts_base_url="https://api.elevenlabs.io",
        tts_api_key="eleven-key",
        tts_model="eleven_multilingual_v2",
        tts_voice="voice/id",
    )
    await synthesis.synthesize("你好", SpeechSettings(), eleven)
    assert "/v1/text-to-speech/voice%2Fid" in str(captured[1].url)
    assert captured[1].headers["xi-api-key"] == "eleven-key"


@pytest.mark.asyncio
async def test_azure_and_elevenlabs_streaming_pcm_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = httpx.AsyncClient
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        content_type = "audio/x-wav" if "microsoft.com" in request.url.host else "audio/mpeg"
        return httpx.Response(200, headers={"content-type": content_type}, content=b"\x01\x00" * 2400)

    def client(*, timeout: float, **_: object) -> httpx.AsyncClient:
        return original(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(synthesis.httpx, "AsyncClient", client)
    azure = Settings.model_construct(
        tts_protocol="azure-speech",
        tts_base_url="https://eastasia.tts.speech.microsoft.com",
        tts_api_key="azure-key",
        tts_voice="zh-CN-XiaoxiaoNeural",
        tts_streaming_enabled=True,
        tts_stream_sample_rate=24000,
    )
    azure_stream = await synthesis.open_synthesis_stream("你好", SpeechSettings(), azure)
    assert b"".join([block async for block in azure_stream.iter_bytes()])
    assert captured[0].headers["x-microsoft-outputformat"] == "raw-24khz-16bit-mono-pcm"
    assert b"zh-CN-XiaoxiaoNeural" in captured[0].content
    assert synthesis.effective_model(azure) == "Azure Neural TTS"
    assert any(
        item["value"] == "zh-CN-XiaoxiaoNeural" for item in synthesis.voice_options(azure)
    )

    await synthesis.close_synthesis_clients()
    eleven = Settings.model_construct(
        tts_protocol="elevenlabs",
        tts_base_url="https://api.elevenlabs.io",
        tts_api_key="eleven-key",
        tts_model="eleven_flash_v2_5",
        tts_voice="voice-id",
        tts_streaming_enabled=True,
        tts_stream_sample_rate=24000,
    )
    eleven_stream = await synthesis.open_synthesis_stream("你好", SpeechSettings(), eleven)
    assert b"".join([block async for block in eleven_stream.iter_bytes()])
    assert "/v1/text-to-speech/voice-id/stream" in str(captured[1].url)
    assert captured[1].url.params["output_format"] == "pcm_24000"
    assert json.loads(captured[1].content)["model_id"] == "eleven_flash_v2_5"


@pytest.mark.asyncio
async def test_siliconflow_enables_provider_streaming_with_matching_sample_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_tail = asyncio.Event()
    closed = False
    captured: list[dict[str, object]] = []

    class AudioStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"\x00\x01" * 2400
            await release_tail.wait()
            yield b"\x00\x02" * 2400

        async def aclose(self) -> None:
            nonlocal closed
            closed = True

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200, headers={"content-type": "application/octet-stream"}, stream=AudioStream(),
        )

    original = httpx.AsyncClient

    def client(*, timeout: float, **_: object) -> httpx.AsyncClient:
        return original(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(synthesis.httpx, "AsyncClient", client)
    settings = Settings.model_construct(
        tts_base_url="https://api.siliconflow.cn/v1", tts_api_key="test-key",
        tts_model="FunAudioLLM/CosyVoice2-0.5B", tts_voice="anna",
        tts_streaming_enabled=True, tts_stream_sample_rate=24000,
    )
    stream = await synthesis.open_synthesis_stream("测试", SpeechSettings(), settings)
    iterator = stream.iter_bytes()
    # The next block is deliberately withheld. First audio must already be usable.
    first = await asyncio.wait_for(anext(iterator), 1)
    assert len(first) == 4800
    assert captured[0]["stream"] is True
    assert captured[0]["sample_rate"] == 24000
    assert captured[0]["voice"] == "FunAudioLLM/CosyVoice2-0.5B:anna"
    await iterator.aclose()
    assert closed


@pytest.mark.asyncio
async def test_volcengine_v3_sse_stream_is_decoded_incrementally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = httpx.AsyncClient
    captured: list[httpx.Request] = []
    first_audio = b"\x01\x00" * 1200
    second_audio = b"\x02\x00" * 1200
    body = (
        "event: 352\n"
        f"data: {json.dumps({'code': 0, 'message': '', 'data': base64.b64encode(first_audio).decode()})}\n\n"
        "event: 352\n"
        f"data: {json.dumps({'code': 0, 'message': '', 'data': base64.b64encode(second_audio).decode()})}\n\n"
        "event: 152\n"
        'data: {"code":20000000,"message":"OK","data":null}\n\n'
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream; charset=utf-8"}, content=body,
        )

    def client(*, timeout: float, **_: object) -> httpx.AsyncClient:
        return original(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(synthesis.httpx, "AsyncClient", client)
    settings = Settings.model_construct(
        tts_protocol="volcengine",
        tts_base_url="https://openspeech.bytedance.com",
        tts_api_key="volc-key",
        tts_model="seed-tts-2.0",
        tts_voice="authorized-speaker",
        tts_streaming_enabled=True,
        tts_stream_sample_rate=24000,
    )
    stream = await synthesis.open_synthesis_stream(
        "你好", SpeechSettings(rate=1.2), settings,
    )
    blocks = [block async for block in stream.iter_bytes()]
    assert blocks == [first_audio, second_audio]
    assert str(captured[0].url).endswith("/api/v3/tts/unidirectional/sse")
    assert captured[0].headers["x-api-key"] == "volc-key"
    assert captured[0].headers["x-api-resource-id"] == "seed-tts-2.0"
    payload = json.loads(captured[0].content)
    assert payload["req_params"]["speaker"] == "authorized-speaker"
    assert payload["req_params"]["audio_params"] == {
        "format": "pcm", "sample_rate": 24000, "speech_rate": 20,
    }


@pytest.mark.asyncio
async def test_volcengine_sse_business_error_is_available_to_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = httpx.AsyncClient
    body = (
        b"event: 152\n"
        b'data: {"code":45000010,"message":"speaker is not authorized: volc-key","data":null}\n\n'
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "x-tt-logid": "volc-trace-123"},
            content=body,
        )

    def client(*, timeout: float, **_: object) -> httpx.AsyncClient:
        return original(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(synthesis.httpx, "AsyncClient", client)
    settings = Settings.model_construct(
        tts_protocol="volcengine",
        tts_base_url="https://openspeech.bytedance.com",
        tts_api_key="volc-key",
        tts_model="seed-tts-2.0",
        tts_voice="BV001_V2_streaming",
        tts_streaming_enabled=True,
        tts_stream_sample_rate=24000,
    )
    stream = await synthesis.open_synthesis_stream("你好", SpeechSettings(), settings)
    with pytest.raises(synthesis.VolcengineTtsError) as error:
        _ = [block async for block in stream.iter_bytes()]

    detail = failure_detail(error.value, "tts", settings)
    assert detail["category"] == "provider"
    assert detail["upstream_code"] == "45000010"
    assert detail["upstream_message"] == "speaker is not authorized: [redacted]"
    assert detail["request_id"] == "volc-trace-123"
    assert "seed-tts-2.0" in detail["suggestion"]
