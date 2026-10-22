"""Read-only model inventory and explicit, audited health probes."""

import asyncio
import io
import json
import math
import struct
import time
import uuid
import wave
import zlib
from datetime import UTC, datetime
from typing import Any, Literal, cast

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.core.config import Settings
from medical_agent.modules.speech.synthesis import effective_voice, is_configured, synthesize
from medical_agent.modules.system.models import AdminOperation
from medical_agent.modules.system.probe_diagnostics import failure_detail, safe_host
from medical_agent.modules.system.schemas import SpeechSettings
from medical_agent.modules.system.speech_service import read_settings
from medical_agent.providers.embedding.client import (
    EmbeddingProviderError,
    build_embedding_provider,
)
from medical_agent.providers.llm.client import (
    DemoChatProvider,
    OpenAICompatibleProvider,
    ProviderError,
)
from medical_agent.providers.llm.protocol import ChatTurn, ImageInput

ModelKind = Literal["chat", "vision", "asr", "embedding", "tts", "browser_tts"]
MODEL_KINDS: tuple[ModelKind, ...] = ("chat", "vision", "asr", "embedding", "tts", "browser_tts")


def _probe_png() -> bytes:
    # Vision providers such as Qwen3-VL require dimensions above 28 pixels.
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)
    pixels = (b"\x00" + bytes((220, 230, 225)) * 64) * 64
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def _host(value: str) -> str:
    return safe_host(value)


def _configured(*values: str) -> bool:
    return all(value.strip() for value in values)


def _cards(settings: Settings) -> list[dict[str, Any]]:
    chat_configured = settings.llm_provider == "demo" or _configured(
        settings.llm_base_url, settings.llm_api_key, settings.llm_model
    )
    return [
        {
            "kind": "chat",
            "label": "文本对话",
            "provider": settings.llm_provider,
            "model": "内置演示" if settings.llm_provider == "demo" else settings.llm_model,
            "endpoint_host": "本地进程"
            if settings.llm_provider == "demo"
            else _host(settings.llm_base_url),
            "api_key_configured": False
            if settings.llm_provider == "demo"
            else bool(settings.llm_api_key),
            "configured": chat_configured,
            "settings": {
                "timeout_seconds": settings.llm_timeout_seconds,
                "max_tokens": settings.llm_max_tokens,
                "thinking": settings.llm_enable_thinking,
            },
        },
        {
            "kind": "vision",
            "label": "图片理解",
            "provider": "openai-compatible" if settings.vision_model else "disabled",
            "model": settings.vision_model,
            "endpoint_host": _host(settings.vision_base_url),
            "api_key_configured": bool(settings.vision_api_key),
            "configured": _configured(
                settings.vision_base_url, settings.vision_api_key, settings.vision_model
            ),
            "settings": {
                "timeout_seconds": settings.llm_timeout_seconds,
                "max_tokens": settings.vision_max_tokens,
                "thinking": settings.vision_enable_thinking,
            },
        },
        {
            "kind": "asr",
            "label": "语音识别",
            "provider": settings.asr_provider,
            "model": settings.asr_model,
            "endpoint_host": _host(settings.asr_base_url),
            "api_key_configured": bool(settings.asr_api_key),
            "configured": settings.asr_provider == "openai-compatible"
            and _configured(settings.asr_base_url, settings.asr_api_key, settings.asr_model),
            "settings": {
                "timeout_seconds": settings.asr_timeout_seconds,
                "language": settings.asr_language,
            },
        },
        {
            "kind": "embedding",
            "label": "文本向量",
            "provider": "openai-compatible" if settings.embedding_model else "disabled",
            "model": settings.embedding_model,
            "endpoint_host": _host(settings.embedding_base_url),
            "api_key_configured": bool(settings.embedding_api_key),
            "configured": _configured(
                settings.embedding_base_url,
                settings.embedding_api_key,
                settings.embedding_model,
            ),
            "settings": {
                "timeout_seconds": settings.embedding_timeout_seconds,
                "batch_size": settings.embedding_batch_size,
            },
        },
        {
            "kind": "tts",
            "label": "云端语音合成",
            "provider": "openai-compatible",
            "model": settings.tts_model,
            "endpoint_host": _host(settings.tts_base_url),
            "api_key_configured": bool(settings.tts_api_key),
            "configured": is_configured(settings),
            "client_only": False,
            "settings": {},
        },
        {
            "kind": "browser_tts",
            "label": "设备语音",
            "provider": "browser",
            "model": "Web Speech Synthesis",
            "endpoint_host": "患者浏览器",
            "api_key_configured": False,
            "configured": True,
            "client_only": True,
            "settings": {},
        },
    ]


def operation_view(row: AdminOperation) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "target_id": row.target_id,
        "status": row.status,
        "latency_ms": row.latency_ms,
        "detail": json.loads(row.detail_json),
        "error": row.error,
        "created_at": row.created_at,
    }


async def model_inventory(db: AsyncSession, settings: Settings) -> dict[str, Any]:
    cards = _cards(settings)
    speech = await read_settings(db)
    tts_card = next(card for card in cards if card["kind"] == "tts")
    tts_card.update(
        configured=is_configured(settings) and bool(effective_voice(speech, settings)),
        settings={
            "voice": effective_voice(speech, settings),
            "rate": speech.rate,
            "timeout_seconds": settings.tts_timeout_seconds,
        },
        active=speech.provider == "openai-compatible",
    )
    cards[-1].update(
        active=speech.provider == "browser",
        settings={"voice": speech.voice or "自动选择本机音色", "language": speech.language},
    )
    for card in cards:
        latest = await db.scalar(
            select(AdminOperation)
            .where(
                AdminOperation.kind == "model_probe",
                AdminOperation.target_id == card["kind"],
            )
            .order_by(AdminOperation.created_at.desc())
            .limit(1)
        )
        # Old browser probes used the cloud row's ID; don't show them as cloud health.
        card["last_probe"] = (
            operation_view(latest)
            if latest and not (card["kind"] == "tts" and latest.status == "client_only")
            else None
        )
    return {
        "items": cards,
        "note": "模型配置读取自后端环境变量；管理端不会返回密钥明文。探活仅在手动触发时调用。",
    }


async def _probe_chat(settings: Settings) -> dict[str, object]:
    if settings.llm_provider == "demo":
        provider = DemoChatProvider()
    else:
        if not _configured(settings.llm_base_url, settings.llm_api_key, settings.llm_model):
            raise RuntimeError("model_not_configured")
        provider = OpenAICompatibleProvider(
            settings.model_copy(update={"llm_max_tokens": 128, "llm_timeout_seconds": 15.0})
        )
    result = await provider.complete(
        [
            ChatTurn(role="system", content="这是服务探活。"),
            ChatTurn(role="user", content="只回复 OK"),
        ]
    )
    return {"response_received": bool(result.strip())}


async def _probe_vision(settings: Settings) -> dict[str, object]:
    if not _configured(settings.vision_base_url, settings.vision_api_key, settings.vision_model):
        raise RuntimeError("model_not_configured")
    provider = OpenAICompatibleProvider(
        settings.model_copy(update={"vision_max_tokens": 128, "llm_timeout_seconds": 15.0})
    )
    result = await provider.complete(
        [
            ChatTurn(role="system", content="这是服务探活。"),
            ChatTurn(
                role="user",
                content="只回复 OK",
                images=(ImageInput(media_type="image/png", data=_probe_png()),),
            ),
        ]
    )
    return {"response_received": bool(result.strip()), "test_image": "64x64_png"}


async def _probe_embedding(settings: Settings) -> dict[str, object]:
    provider = build_embedding_provider(settings)
    if provider is None:
        raise RuntimeError("model_not_configured")
    vectors = await provider.embed(["health check"])
    if not vectors or not vectors[0] or any(not math.isfinite(value) for value in vectors[0]):
        raise EmbeddingProviderError("embedding_response_invalid")
    return {"dimension": len(vectors[0])}


def _silent_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * 4_000)
    return buffer.getvalue()


async def _probe_asr(settings: Settings) -> dict[str, object]:
    if settings.asr_provider != "openai-compatible" or not _configured(
        settings.asr_base_url, settings.asr_api_key, settings.asr_model
    ):
        raise RuntimeError("model_not_configured")
    form = {"model": settings.asr_model, "response_format": "json"}
    if settings.asr_language.strip():
        form["language"] = settings.asr_language.strip()
    async with httpx.AsyncClient(timeout=min(settings.asr_timeout_seconds, 15.0)) as client:
        response = await client.post(
            f"{settings.asr_base_url.rstrip('/')}/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.asr_api_key}"},
            data=form,
            files={"file": ("health-check.wav", _silent_wav(), "audio/wav")},
        )
        response.raise_for_status()
        raw_payload = cast(object, response.json())
        payload = cast(dict[str, object], raw_payload) if isinstance(raw_payload, dict) else {}
    if not isinstance(payload.get("text"), str):
        raise TypeError("asr_response_invalid")
    return {"response_received": True, "test_audio": "250ms_silence"}


async def _execute_probe(kind: ModelKind, settings: Settings) -> dict[str, object]:
    if kind == "browser_tts":
        return {"client_only": True, "message": "由患者浏览器提供，需在实际设备检查。"}
    async with asyncio.timeout(16):
        if kind == "chat":
            return await _probe_chat(settings)
        if kind == "vision":
            return await _probe_vision(settings)
        if kind == "asr":
            return await _probe_asr(settings)
        return await _probe_embedding(settings)


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return exc.detail if exc.detail.startswith("tts_") else "probe_failed"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"upstream_http_{exc.response.status_code}"
    if isinstance(exc, TimeoutError):
        return "probe_timeout"
    if isinstance(exc, (ProviderError, EmbeddingProviderError, RuntimeError, TypeError)):
        value = str(exc)
        allowed = {
            "model_not_configured",
            "model_request_failed",
            "model_response_empty",
            "model_response_invalid",
            "embedding_request_failed",
            "embedding_response_invalid",
            "embedding_count_mismatch",
            "asr_response_invalid",
        }
        return value if value in allowed else "probe_failed"
    if isinstance(exc, (httpx.HTTPError, ValueError, json.JSONDecodeError)):
        return "probe_failed"
    return "probe_failed"


async def probe_model(
    db: AsyncSession,
    kind: ModelKind,
    settings: Settings,
    speech_override: SpeechSettings | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    detail: dict[str, object] = {}
    error = ""
    status = "client_only" if kind == "browser_tts" else "healthy"
    try:
        speech = (speech_override or await read_settings(db)) if kind == "tts" else None
        if speech is not None:
            async with asyncio.timeout(settings.tts_timeout_seconds):
                audio = await synthesize("语音服务测试。", speech, settings)
            status = "healthy"
            detail = {"response_received": True, "audio_bytes": len(audio)}
        else:
            detail = await _execute_probe(kind, settings)
    except Exception as exc:  # noqa: BLE001 - provider failures must become sanitized probe results.
        error = _safe_error(exc)
        detail = failure_detail(exc, kind, settings)
        if detail["category"] == "configuration":
            error = "model_not_configured"
        status = "not_configured" if error == "model_not_configured" else "unhealthy"
    if kind == "tts":
        preferences = speech_override or await read_settings(db)
        detail.update(
            model=settings.tts_model,
            voice=effective_voice(preferences, settings),
            configuration="试听表单" if speech_override else "已保存设置",
        )
    row = AdminOperation(
        id=str(uuid.uuid4()),
        kind="model_probe",
        target_id=kind,
        status=status,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
        detail_json=json.dumps(detail, ensure_ascii=False),
        error=error,
        created_at=datetime.now(UTC),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return operation_view(row)
