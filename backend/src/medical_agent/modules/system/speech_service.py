"""Persist device speech defaults; credentials are never part of this contract."""

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.core.config import get_settings
from medical_agent.modules.speech.synthesis import is_configured
from medical_agent.modules.system.models import RuntimeSetting
from medical_agent.modules.system.prompt_service import audit
from medical_agent.modules.system.schemas import SpeechSettings

KEY = "speech"


async def read_settings(db: AsyncSession) -> SpeechSettings:
    row = await db.get(RuntimeSetting, KEY)
    return SpeechSettings.model_validate_json(row.value_json) if row else SpeechSettings()


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
    return payload
