"""Persist device speech defaults; credentials are never part of this contract."""

import asyncio
import time

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.core.config import get_settings
from medical_agent.modules.speech.synthesis import is_configured
from medical_agent.modules.system.models import RuntimeSetting
from medical_agent.modules.system.prompt_service import audit
from medical_agent.modules.system.schemas import SpeechSettings

KEY = "speech"
_CACHE_TTL_SECONDS = 5.0
_settings_cache: dict[int, tuple[float, SpeechSettings]] = {}
_cache_lock = asyncio.Lock()


def _cache_key(db: AsyncSession) -> int:
    """Scope snapshots to one engine so isolated test/app databases never mix."""
    return id(db.bind)


def clear_settings_cache() -> None:
    """Clear process-local snapshots during tests or controlled lifecycle resets."""
    _settings_cache.clear()


async def read_settings(db: AsyncSession) -> SpeechSettings:
    row = await db.get(RuntimeSetting, KEY)
    return SpeechSettings.model_validate_json(row.value_json) if row else SpeechSettings()


async def read_settings_cached(db: AsyncSession) -> SpeechSettings:
    """Return a short-lived snapshot without stampeding the database for TTS segments."""
    key = _cache_key(db)
    now = time.monotonic()
    cached = _settings_cache.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]
    async with _cache_lock:
        now = time.monotonic()
        cached = _settings_cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
        value = await read_settings(db)
        if len(_settings_cache) >= 16:
            _settings_cache.clear()
        _settings_cache[key] = (now + _CACHE_TTL_SECONDS, value)
        return value


async def save_settings(db: AsyncSession, payload: SpeechSettings) -> SpeechSettings:
    if payload.provider == "openai-compatible":
        settings = get_settings()
        if not is_configured(settings):
            raise HTTPException(422, "tts_not_configured")
        if not (payload.cloud_voice or settings.tts_voice):
            raise HTTPException(422, "tts_voice_required")
    row = await db.get(RuntimeSetting, KEY)
    if row is None:
        row = RuntimeSetting(key=KEY, value_json=payload.model_dump_json())
        db.add(row)
    else:
        row.value_json = payload.model_dump_json()
    audit(db, kind="speech_settings_updated", target_id=KEY, settings=payload.model_dump())
    await db.commit()
    _settings_cache[_cache_key(db)] = (time.monotonic() + _CACHE_TTL_SECONDS, payload)
    return payload
