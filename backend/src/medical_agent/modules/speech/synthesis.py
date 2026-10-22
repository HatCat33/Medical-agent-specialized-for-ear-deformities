"""Bounded, ephemeral synthesis through a server-configured compatible TTS service."""

import asyncio
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from medical_agent.core.config import Settings
from medical_agent.core.logging import get_logger
from medical_agent.modules.system.schemas import SpeechSettings

logger = get_logger(__name__)
_slots = asyncio.Semaphore(4)


def voice_options(settings: Settings) -> list[dict[str, str]]:
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


def effective_voice(preferences: SpeechSettings, settings: Settings) -> str:
    voice = (preferences.cloud_voice or settings.tts_voice).strip()
    for option in voice_options(settings):
        if voice == option["label"]:
            return option["value"]
    return voice


def is_configured(settings: Settings) -> bool:
    return all(
        value.strip() for value in (settings.tts_base_url, settings.tts_api_key, settings.tts_model)
    )


async def synthesize(text: str, preferences: SpeechSettings, settings: Settings) -> bytes:
    if not is_configured(settings):
        raise HTTPException(503, "tts_not_configured")
    voice = effective_voice(preferences, settings)
    if not voice:
        raise HTTPException(422, "tts_voice_required")
    if _slots.locked():
        raise HTTPException(429, "tts_busy")
    try:
        async with (
            _slots,
            asyncio.timeout(settings.tts_timeout_seconds),
            httpx.AsyncClient(timeout=settings.tts_timeout_seconds) as client,
            client.stream(
                "POST",
                f"{settings.tts_base_url.rstrip('/')}/audio/speech",
                headers={"Authorization": f"Bearer {settings.tts_api_key}"},
                json={
                    "model": settings.tts_model,
                    "voice": voice,
                    "input": text,
                    "response_format": "mp3",
                    "speed": preferences.rate,
                },
            ) as response,
        ):
            if response.is_error:
                # Preserve a bounded error body for the protected admin diagnostics.
                error_body = bytearray()
                async for block in response.aiter_bytes():
                    error_body.extend(block[: 8192 - len(error_body)])
                    if len(error_body) >= 8192:
                        break
                error_response = httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=bytes(error_body),
                    request=response.request,
                )
                error_response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";")[0]
            if content_type not in {"audio/mpeg", "audio/mp3", "application/octet-stream"}:
                raise HTTPException(502, "tts_response_invalid")
            audio = bytearray()
            async for block in response.aiter_bytes():
                audio.extend(block)
                if len(audio) > 8 * 1024 * 1024:
                    raise HTTPException(502, "tts_audio_too_large")
            if not audio:
                raise HTTPException(502, "tts_response_invalid")
            logger.info("speech_synthesis_completed", characters=len(text), audio_bytes=len(audio))
            return bytes(audio)
    except httpx.HTTPStatusError as exc:
        logger.warning("speech_synthesis_failed", upstream_status=exc.response.status_code)
        raise HTTPException(502, "tts_request_failed") from exc
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise HTTPException(504, "tts_timeout") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "tts_request_failed") from exc
