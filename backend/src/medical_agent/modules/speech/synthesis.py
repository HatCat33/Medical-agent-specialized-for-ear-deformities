"""Bounded, ephemeral synthesis through pluggable cloud TTS protocols."""

import asyncio
import base64
import json
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from urllib.parse import quote, urlsplit
from xml.sax.saxutils import escape, quoteattr

import httpx
from fastapi import HTTPException

from medical_agent.core.config import Settings
from medical_agent.core.logging import get_logger
from medical_agent.modules.speech.local_tts import (
    LocalSynthesisStream,
    is_local_configured,
    local_pcm,
    local_wav,
)
from medical_agent.modules.system.schemas import SpeechSettings

logger = get_logger(__name__)
_slots = asyncio.Semaphore(4)
_clients: dict[tuple[str, float, int], httpx.AsyncClient] = {}
_client_lock = asyncio.Lock()
_MAX_AUDIO_BYTES = 8 * 1024 * 1024


class VolcengineTtsError(Exception):
    """A sanitized Volcengine business error returned inside an SSE response."""

    def __init__(self, code: object, message: object, request_id: str = "") -> None:
        super().__init__("volcengine_tts_error")
        self.upstream_code = str(code)[:100]
        self.upstream_message = " ".join(str(message or "").split())[:700]
        self.request_id = request_id[:120]


@dataclass(frozen=True)
class _UpstreamRequest:
    url: str
    headers: dict[str, str]
    params: dict[str, str]
    json: dict[str, object] | None = None
    content: str | None = None


class SynthesisStream:
    """Own an upstream response and concurrency slot until the client finishes or aborts."""

    def __init__(
        self,
        response: httpx.Response,
        *,
        characters: int,
        queue_ms: float,
        upstream_at: float,
    ) -> None:
        self.response = response
        self.characters = characters
        self.queue_ms = queue_ms
        self.upstream_at = upstream_at
        self._closed = False

    async def iter_bytes(self) -> AsyncGenerator[bytes, None]:
        audio_bytes = 0
        first_audio_ms: float | None = None
        chunks = 0
        try:
            async for block in self.response.aiter_bytes():
                if not block:
                    continue
                chunks += 1
                if first_audio_ms is None:
                    first_audio_ms = round((time.monotonic() - self.upstream_at) * 1000, 2)
                    logger.info(
                        "speech_stream_first_audio", characters=self.characters,
                        first_audio_ms=first_audio_ms, queue_ms=self.queue_ms,
                    )
                audio_bytes += len(block)
                if audio_bytes > _MAX_AUDIO_BYTES:
                    raise HTTPException(502, "tts_audio_too_large")
                yield block
            if not audio_bytes:
                raise HTTPException(502, "tts_response_invalid")
            logger.info(
                "speech_stream_completed",
                characters=self.characters,
                audio_bytes=audio_bytes,
                queue_ms=self.queue_ms,
                first_audio_ms=first_audio_ms,
                chunks=chunks,
                upstream_ms=round((time.monotonic() - self.upstream_at) * 1000, 2),
            )
        except asyncio.CancelledError:
            logger.info("speech_stream_cancelled", characters=self.characters)
            raise
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            logger.warning("speech_stream_failed", reason=type(exc).__name__)
            raise
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.response.aclose()
        finally:
            _slots.release()


class VolcengineSseStream:
    """Decode Volcengine V3 SSE base64 events into a raw audio stream."""

    def __init__(
        self,
        response: httpx.Response,
        *,
        characters: int,
        queue_ms: float,
        upstream_at: float,
    ) -> None:
        self.response = response
        self.characters = characters
        self.queue_ms = queue_ms
        self.upstream_at = upstream_at
        self._closed = False

    async def iter_bytes(self) -> AsyncGenerator[bytes, None]:
        audio_bytes = 0
        first_audio_ms: float | None = None
        chunks = 0
        finished = False
        try:
            async for line in self.response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError as exc:
                    raise HTTPException(502, "tts_stream_response_invalid") from exc
                code = payload.get("code")
                encoded = payload.get("data")
                if code == 0 and isinstance(encoded, str) and encoded:
                    try:
                        block = base64.b64decode(encoded, validate=True)
                    except (ValueError, TypeError) as exc:
                        raise HTTPException(502, "tts_stream_response_invalid") from exc
                    if not block:
                        continue
                    chunks += 1
                    if first_audio_ms is None:
                        first_audio_ms = round((time.monotonic() - self.upstream_at) * 1000, 2)
                        logger.info(
                            "speech_stream_first_audio",
                            provider="volcengine",
                            characters=self.characters,
                            first_audio_ms=first_audio_ms,
                            queue_ms=self.queue_ms,
                        )
                    audio_bytes += len(block)
                    if audio_bytes > _MAX_AUDIO_BYTES:
                        raise HTTPException(502, "tts_audio_too_large")
                    yield block
                elif code == 20000000:
                    finished = True
                elif isinstance(code, int) and code not in {0, 20000000}:
                    request_id = (
                        self.response.headers.get("x-tt-logid", "")
                        or self.response.headers.get("x-api-request-id", "")
                    )
                    logger.warning(
                        "speech_stream_failed",
                        provider="volcengine",
                        upstream_code=code,
                        request_id=request_id,
                    )
                    raise VolcengineTtsError(code, payload.get("message"), request_id)
            if not finished or not audio_bytes:
                raise HTTPException(502, "tts_response_invalid")
            logger.info(
                "speech_stream_completed",
                provider="volcengine",
                characters=self.characters,
                audio_bytes=audio_bytes,
                queue_ms=self.queue_ms,
                first_audio_ms=first_audio_ms,
                chunks=chunks,
                upstream_ms=round((time.monotonic() - self.upstream_at) * 1000, 2),
            )
        except asyncio.CancelledError:
            logger.info(
                "speech_stream_cancelled", provider="volcengine", characters=self.characters,
            )
            raise
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.response.aclose()
        finally:
            _slots.release()


async def _client_for(settings: Settings) -> httpx.AsyncClient:
    key = (
        settings.tts_base_url.rstrip("/"),
        settings.tts_timeout_seconds,
        id(httpx.AsyncClient),
    )
    existing = _clients.get(key)
    if existing is not None:
        return existing
    async with _client_lock:
        existing = _clients.get(key)
        if existing is None:
            existing = httpx.AsyncClient(
                timeout=settings.tts_timeout_seconds,
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            )
            _clients[key] = existing
        return existing


async def close_synthesis_clients() -> None:
    clients = list(_clients.values())
    _clients.clear()
    await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)


def voice_options(settings: Settings) -> list[dict[str, str]]:
    if settings.tts_protocol == "local-sherpa":
        return [{"label": "中文本地音色 · Huayan", "value": "huayan:0"}]
    if settings.tts_protocol == "azure-speech":
        return [
            {"label": "晓晓 · 女声 · 温和自然", "value": "zh-CN-XiaoxiaoNeural"},
            {"label": "晓伊 · 女声 · 亲切", "value": "zh-CN-XiaoyiNeural"},
            {"label": "云希 · 男声 · 自然", "value": "zh-CN-YunxiNeural"},
            {"label": "云健 · 男声 · 稳重", "value": "zh-CN-YunjianNeural"},
        ]
    host = urlsplit(settings.tts_base_url).hostname
    if host in {"api.siliconflow.cn", "api.siliconflow.com"} and settings.tts_model in {
        "FunAudioLLM/CosyVoice2-0.5B",
        "FunAudioLLM/CosyVoice-300M-SFT",
    }:
        return [
            {"label": name, "value": f"{settings.tts_model}:{name}"}
            for name in ("alex", "anna", "bella", "benjamin", "charles", "david")
        ]
    return []


def effective_model(settings: Settings) -> str:
    if settings.tts_protocol == "local-sherpa":
        return "Huayan Chinese (CPU)"
    if settings.tts_protocol == "azure-speech":
        return "Azure Neural TTS"
    if settings.tts_protocol == "volcengine":
        return settings.tts_model or "seed-tts-2.0"
    return settings.tts_model


def effective_voice(preferences: SpeechSettings, settings: Settings) -> str:
    if settings.tts_protocol == "local-sherpa":
        return f"huayan:{settings.tts_local_speaker_id}"
    voice = (preferences.cloud_voice or settings.tts_voice).strip()
    for option in voice_options(settings):
        if voice == option["label"]:
            return option["value"]
    return voice


def is_configured(settings: Settings) -> bool:
    if settings.tts_protocol == "local-sherpa":
        return is_local_configured(settings)
    common = bool(settings.tts_base_url.strip() and settings.tts_api_key.strip())
    if settings.tts_protocol in {"azure-speech", "custom"}:
        return common
    if settings.tts_protocol in {"elevenlabs", "volcengine"}:
        return common and bool(settings.tts_model.strip())
    return common and bool(settings.tts_model.strip())


def _azure_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    return value if value.endswith("/cognitiveservices/v1") else f"{value}/cognitiveservices/v1"


def _volcengine_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    suffix = "/api/v3/tts/unidirectional/sse"
    return value if value.endswith(suffix) else f"{value}{suffix}"


def _volcengine_request(
    text: str,
    preferences: SpeechSettings,
    settings: Settings,
    *,
    streaming: bool,
) -> _UpstreamRequest:
    voice = effective_voice(preferences, settings)
    if not voice:
        raise HTTPException(422, "tts_voice_required")
    speech_rate = round((min(2.0, max(0.5, preferences.rate)) - 1) * 100)
    return _UpstreamRequest(
        url=_volcengine_url(settings.tts_base_url),
        headers={
            "X-Api-Key": settings.tts_api_key,
            "X-Api-Resource-Id": settings.tts_model,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "Accept": "text/event-stream",
        },
        params={},
        json={
            "user": {"uid": str(uuid.uuid4())},
            "req_params": {
                "text": text,
                "speaker": voice,
                "audio_params": {
                    "format": "pcm" if streaming else "mp3",
                    "sample_rate": settings.tts_stream_sample_rate,
                    "speech_rate": speech_rate,
                },
            },
        },
    )


async def _open_volcengine_stream(
    text: str,
    preferences: SpeechSettings,
    settings: Settings,
    *,
    streaming: bool,
) -> VolcengineSseStream:
    request = _volcengine_request(text, preferences, settings, streaming=streaming)
    client = await _client_for(settings)
    queued_at = time.monotonic()
    acquired = False
    response: httpx.Response | None = None
    succeeded = False
    try:
        await asyncio.wait_for(_slots.acquire(), timeout=settings.tts_timeout_seconds)
        acquired = True
        queue_ms = round((time.monotonic() - queued_at) * 1000, 2)
        upstream_at = time.monotonic()
        response = await client.send(
            client.build_request(
                "POST", request.url, headers=request.headers, json=request.json,
            ),
            stream=True,
        )
        if response.is_error:
            (await _bounded_error(response)).raise_for_status()
        content_type = response.headers.get("content-type", "").split(";")[0].lower()
        if content_type != "text/event-stream":
            raise HTTPException(502, "tts_stream_response_invalid")
        succeeded = True
        return VolcengineSseStream(
            response,
            characters=len(text),
            queue_ms=queue_ms,
            upstream_at=upstream_at,
        )
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "speech_stream_open_failed",
            provider="volcengine",
            upstream_status=exc.response.status_code,
        )
        raise HTTPException(502, "tts_request_failed") from exc
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise HTTPException(504, "tts_timeout") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "tts_request_failed") from exc
    finally:
        if not succeeded:
            if response is not None:
                await response.aclose()
            if acquired:
                _slots.release()


def _upstream_request(
    text: str,
    preferences: SpeechSettings,
    settings: Settings,
    *,
    streaming: bool,
) -> _UpstreamRequest:
    voice = effective_voice(preferences, settings)
    protocol = settings.tts_protocol
    if not voice and not (protocol == "custom" and not settings.tts_custom_voice_field.strip()):
        raise HTTPException(422, "tts_voice_required")
    base_url = settings.tts_base_url.rstrip("/")
    if protocol == "azure-speech":
        output_format = (
            f"raw-{settings.tts_stream_sample_rate // 1000}khz-16bit-mono-pcm"
            if streaming
            else "audio-24khz-48kbitrate-mono-mp3"
        )
        rate = round((preferences.rate - 1) * 100)
        ssml = (
            '<speak version="1.0" xml:lang="zh-CN"><voice name='
            f"{quoteattr(voice)}><prosody rate=\"{rate:+d}%\">{escape(text)}</prosody>"
            "</voice></speak>"
        )
        return _UpstreamRequest(
            url=_azure_url(base_url),
            headers={
                "Ocp-Apim-Subscription-Key": settings.tts_api_key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": output_format,
                "User-Agent": "medical-agent",
            },
            params={},
            content=ssml,
        )
    if protocol == "elevenlabs":
        suffix = "/stream" if streaming else ""
        output_format = (
            f"pcm_{settings.tts_stream_sample_rate}"
            if streaming
            else "mp3_44100_128"
        )
        return _UpstreamRequest(
            url=f"{base_url}/v1/text-to-speech/{quote(voice, safe='')}{suffix}",
            headers={"xi-api-key": settings.tts_api_key, "Accept": "application/octet-stream"},
            params={"output_format": output_format},
            json={
                "text": text,
                "model_id": settings.tts_model,
                "voice_settings": {"speed": min(1.2, max(0.7, preferences.rate))},
            },
        )
    if protocol == "custom":
        path = settings.tts_custom_path.strip() or "/audio/speech"
        url = path if path.startswith(("http://", "https://")) else f"{base_url}/{path.lstrip('/')}"
        scheme = settings.tts_custom_auth_scheme.strip()
        auth_value = f"{scheme} {settings.tts_api_key}".strip()
        payload: dict[str, object] = {
            settings.tts_custom_text_field: text,
            settings.tts_custom_model_field: settings.tts_model,
            settings.tts_custom_voice_field: voice,
            settings.tts_custom_format_field: settings.tts_stream_response_format if streaming else "mp3",
            settings.tts_custom_speed_field: preferences.rate,
        }
        return _UpstreamRequest(
            url=url,
            headers={settings.tts_custom_auth_header: auth_value},
            params={},
            json={key: value for key, value in payload.items() if key},
        )
    payload: dict[str, object] = {
        "model": settings.tts_model,
        "voice": voice,
        "input": text,
        "response_format": settings.tts_stream_response_format if streaming else "mp3",
        "speed": preferences.rate,
    }
    # SiliconFlow exposes explicit stream and sample_rate controls. PCM alone
    # doesn't request incremental generation or guarantee a particular sample rate.
    if urlsplit(base_url).hostname in {"api.siliconflow.cn", "api.siliconflow.com"}:
        payload["stream"] = streaming
        if streaming:
            if settings.tts_stream_channels != 1:
                raise HTTPException(422, "tts_stream_requires_mono")
            payload["sample_rate"] = settings.tts_stream_sample_rate
    return _UpstreamRequest(
        url=f"{base_url}/audio/speech",
        headers={"Authorization": f"Bearer {settings.tts_api_key}"},
        params={},
        json=payload,
    )


async def _bounded_error(response: httpx.Response) -> httpx.Response:
    body = bytearray()
    async for block in response.aiter_bytes():
        body.extend(block[: 8192 - len(body)])
        if len(body) >= 8192:
            break
    return httpx.Response(
        response.status_code,
        headers=response.headers,
        content=bytes(body),
        request=response.request,
    )


def _valid_content_type(
    response: httpx.Response,
    *,
    streaming: bool,
    protocol: str,
) -> bool:
    content_type = response.headers.get("content-type", "").split(";")[0].lower()
    allowed = {"application/octet-stream"}
    if streaming:
        allowed.update({"audio/pcm", "audio/l16", "audio/raw", "audio/x-pcm"})
        if protocol == "azure-speech":
            allowed.update({"audio/x-wav", "audio/wav"})
        # ElevenLabs documents audio/mpeg as its response media type even when
        # output_format explicitly requests headerless PCM.
        if protocol == "elevenlabs":
            allowed.add("audio/mpeg")
    else:
        allowed.update({"audio/mpeg", "audio/mp3"})
    return content_type in allowed


async def synthesize(text: str, preferences: SpeechSettings, settings: Settings) -> bytes:
    if settings.tts_protocol == "local-sherpa":
        return await local_wav(text, preferences, settings)
    if not is_configured(settings):
        raise HTTPException(503, "tts_not_configured")
    if settings.tts_protocol == "volcengine":
        stream = await _open_volcengine_stream(
            text, preferences, settings, streaming=False,
        )
        return b"".join([block async for block in stream.iter_bytes()])
    request = _upstream_request(text, preferences, settings, streaming=False)
    client = await _client_for(settings)
    queued_at = time.monotonic()
    try:
        async with asyncio.timeout(settings.tts_timeout_seconds):
            async with _slots:
                queue_ms = round((time.monotonic() - queued_at) * 1000, 2)
                upstream_at = time.monotonic()
                async with client.stream(
                    "POST",
                    request.url,
                    headers=request.headers,
                    params=request.params,
                    json=request.json,
                    content=request.content,
                ) as response:
                    if response.is_error:
                        (await _bounded_error(response)).raise_for_status()
                    if not _valid_content_type(
                        response, streaming=False, protocol=settings.tts_protocol,
                    ):
                        raise HTTPException(502, "tts_response_invalid")
                    audio = bytearray()
                    async for block in response.aiter_bytes():
                        audio.extend(block)
                        if len(audio) > _MAX_AUDIO_BYTES:
                            raise HTTPException(502, "tts_audio_too_large")
                    if not audio:
                        raise HTTPException(502, "tts_response_invalid")
                    logger.info(
                        "speech_synthesis_completed",
                        characters=len(text),
                        audio_bytes=len(audio),
                        queue_ms=queue_ms,
                        upstream_ms=round((time.monotonic() - upstream_at) * 1000, 2),
                    )
                    return bytes(audio)
    except httpx.HTTPStatusError as exc:
        logger.warning("speech_synthesis_failed", upstream_status=exc.response.status_code)
        raise HTTPException(502, "tts_request_failed") from exc
    except (httpx.TimeoutException, TimeoutError) as exc:
        logger.warning(
            "speech_synthesis_failed",
            reason="timeout",
            characters=len(text),
            elapsed_ms=round((time.monotonic() - queued_at) * 1000, 2),
        )
        raise HTTPException(504, "tts_timeout") from exc
    except httpx.HTTPError as exc:
        logger.warning("speech_synthesis_failed", reason=type(exc).__name__)
        raise HTTPException(502, "tts_request_failed") from exc


async def open_synthesis_stream(
    text: str,
    preferences: SpeechSettings,
    settings: Settings,
) -> SynthesisStream | LocalSynthesisStream | VolcengineSseStream:
    if not settings.tts_streaming_enabled:
        raise HTTPException(404, "tts_streaming_disabled")
    if not is_configured(settings):
        raise HTTPException(503, "tts_not_configured")
    if settings.tts_protocol == "local-sherpa":
        iterator = local_pcm(text, preferences, settings)
        first = await anext(iterator)
        return LocalSynthesisStream(first, iterator)
    if settings.tts_protocol == "volcengine":
        return await _open_volcengine_stream(text, preferences, settings, streaming=True)
    request = _upstream_request(text, preferences, settings, streaming=True)
    client = await _client_for(settings)
    queued_at = time.monotonic()
    acquired = False
    response: httpx.Response | None = None
    succeeded = False
    try:
        await asyncio.wait_for(_slots.acquire(), timeout=settings.tts_timeout_seconds)
        acquired = True
        queue_ms = round((time.monotonic() - queued_at) * 1000, 2)
        upstream_at = time.monotonic()
        upstream_request = client.build_request(
            "POST",
            request.url,
            headers=request.headers,
            params=request.params,
            json=request.json,
            content=request.content,
        )
        response = await client.send(upstream_request, stream=True)
        if response.is_error:
            (await _bounded_error(response)).raise_for_status()
        if not _valid_content_type(
            response, streaming=True, protocol=settings.tts_protocol,
        ):
            raise HTTPException(502, "tts_stream_response_invalid")
        succeeded = True
        return SynthesisStream(
            response,
            characters=len(text),
            queue_ms=queue_ms,
            upstream_at=upstream_at,
        )
    except httpx.HTTPStatusError as exc:
        logger.warning("speech_stream_open_failed", upstream_status=exc.response.status_code)
        raise HTTPException(502, "tts_request_failed") from exc
    except (httpx.TimeoutException, TimeoutError) as exc:
        logger.warning("speech_stream_open_failed", reason="timeout", characters=len(text))
        raise HTTPException(504, "tts_timeout") from exc
    except httpx.HTTPError as exc:
        logger.warning("speech_stream_open_failed", reason=type(exc).__name__)
        raise HTTPException(502, "tts_request_failed") from exc
    finally:
        # A successful stream owns both resources until its iterator finishes.
        if not succeeded:
            if response is not None:
                await response.aclose()
            if acquired:
                _slots.release()
