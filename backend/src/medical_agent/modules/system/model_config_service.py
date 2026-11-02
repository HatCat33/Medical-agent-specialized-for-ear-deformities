"""Persist write-only model credentials and apply model overrides at runtime."""

import json
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.core.config import Settings, apply_runtime_model_overrides, get_settings
from medical_agent.modules.system.models import RuntimeSetting
from medical_agent.modules.system.prompt_service import audit
from medical_agent.modules.system.schemas import ModelConfigurationUpdate

SETTING_KEY = "model_configuration"

KIND_FIELDS: dict[str, tuple[str, ...]] = {
    "chat": (
        "llm_provider", "llm_base_url", "llm_api_key", "llm_model",
        "llm_timeout_seconds", "llm_max_tokens", "llm_enable_thinking", "llm_retry_count",
    ),
    "chat_fallback": (
        "fallback_llm_base_url", "fallback_llm_api_key", "fallback_llm_model",
        "fallback_llm_timeout_seconds", "fallback_llm_max_tokens",
        "fallback_llm_enable_thinking",
    ),
    "vision": (
        "vision_base_url", "vision_api_key", "vision_model", "vision_max_tokens",
        "vision_enable_thinking",
    ),
    "asr": (
        "asr_provider", "asr_base_url", "asr_api_key", "asr_model",
        "asr_language", "asr_timeout_seconds",
    ),
    "embedding": (
        "embedding_base_url", "embedding_api_key", "embedding_model",
        "embedding_timeout_seconds", "embedding_batch_size",
    ),
    "reranker": (
        "reranker_enabled", "reranker_base_url", "reranker_api_key", "reranker_model",
        "reranker_timeout_seconds", "reranker_candidate_k",
    ),
    "tts": (
        "tts_protocol", "tts_base_url", "tts_api_key", "tts_model", "tts_voice",
        "tts_timeout_seconds", "tts_streaming_enabled",
    ),
}

SECRET_FIELDS = {
    "llm_api_key", "fallback_llm_api_key", "vision_api_key", "asr_api_key",
    "embedding_api_key", "reranker_api_key", "tts_api_key",
}


def _public_name(field: str) -> str:
    for prefix in (
        "fallback_llm_", "llm_", "vision_", "asr_", "embedding_", "reranker_", "tts_"
    ):
        if field.startswith(prefix):
            return field.removeprefix(prefix)
    return field


async def _read_overrides(db: AsyncSession) -> dict[str, Any]:
    row = await db.get(RuntimeSetting, SETTING_KEY)
    if row is None:
        return {}
    value = json.loads(row.value_json)
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


async def load_runtime_model_configuration(db: AsyncSession) -> Settings:
    return apply_runtime_model_overrides(await _read_overrides(db))


def configuration_view(kind: str, settings: Settings) -> dict[str, Any]:
    fields = KIND_FIELDS[kind]
    values = {
        _public_name(field): getattr(settings, field)
        for field in fields
        if field not in SECRET_FIELDS
    }
    secret_field = next((field for field in fields if field in SECRET_FIELDS), None)
    return {
        "kind": kind,
        "values": values,
        "api_key_configured": bool(secret_field and getattr(settings, secret_field)),
    }


async def list_configuration(db: AsyncSession) -> dict[str, Any]:
    await load_runtime_model_configuration(db)
    settings = get_settings()
    overrides = await _read_overrides(db)
    return {
        "items": [configuration_view(kind, settings) for kind in KIND_FIELDS],
        "overridden_kinds": [
            kind for kind, fields in KIND_FIELDS.items() if any(field in overrides for field in fields)
        ],
    }


async def save_configuration(
    db: AsyncSession,
    *,
    kind: str,
    payload: ModelConfigurationUpdate,
) -> dict[str, Any]:
    if kind not in KIND_FIELDS:
        raise KeyError(kind)
    overrides = await _read_overrides(db)
    submitted = payload.model_dump(exclude_unset=True)
    api_key = submitted.pop("api_key", None)
    mapping = {_public_name(field): field for field in KIND_FIELDS[kind]}
    for public_name, value in submitted.items():
        field = mapping.get(public_name)
        if field is not None:
            overrides[field] = value
    secret_field = next((field for field in KIND_FIELDS[kind] if field in SECRET_FIELDS), None)
    if secret_field and isinstance(api_key, str) and api_key:
        overrides[secret_field] = api_key

    # Validate the complete effective configuration before persisting it.
    settings = apply_runtime_model_overrides(overrides)
    row = await db.get(RuntimeSetting, SETTING_KEY)
    encoded = json.dumps(overrides, ensure_ascii=False, separators=(",", ":"))
    if row is None:
        db.add(RuntimeSetting(key=SETTING_KEY, value_json=encoded))
    else:
        row.value_json = encoded
    audit(
        db,
        kind="model_configuration_updated",
        target_id=kind,
        status="completed",
        changed_fields=sorted(key for key in submitted if key != "api_key"),
        api_key_updated=bool(api_key),
    )
    await db.commit()
    return configuration_view(kind, settings)


async def reset_configuration(db: AsyncSession, *, kind: str) -> dict[str, Any]:
    if kind not in KIND_FIELDS:
        raise KeyError(kind)
    overrides = await _read_overrides(db)
    for field in KIND_FIELDS[kind]:
        overrides.pop(field, None)
    settings = apply_runtime_model_overrides(overrides)
    row = await db.get(RuntimeSetting, SETTING_KEY)
    if row is not None:
        row.value_json = json.dumps(overrides, ensure_ascii=False, separators=(",", ":"))
    audit(db, kind="model_configuration_reset", target_id=kind, status="completed")
    await db.commit()
    return configuration_view(kind, settings)
