"""Protected prompt and model operations."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.api.dependencies import require_admin_token
from medical_agent.core.config import get_settings
from medical_agent.core.database import get_db
from medical_agent.modules.speech.synthesis import (
    effective_voice,
    is_configured,
    synthesize,
    voice_options,
)
from medical_agent.modules.system import model_service, prompt_service, speech_service
from medical_agent.modules.system.model_service import MODEL_KINDS
from medical_agent.modules.system.models import AdminOperation
from medical_agent.modules.system.probe_diagnostics import failure_detail
from medical_agent.modules.system.schemas import (
    PromptCreate,
    PromptUpdate,
    SpeechPreviewRequest,
    SpeechSettings,
)

router = APIRouter(
    prefix="/admin/system",
    tags=["admin-system"],
    dependencies=[Depends(require_admin_token)],
)
Db = Annotated[AsyncSession, Depends(get_db)]


@router.get("/operations")
async def system_operations(db: Db, page: int = Query(default=1, ge=1)) -> dict[str, Any]:
    rows = await db.scalars(
        select(AdminOperation)
        .order_by(AdminOperation.created_at.desc())
        .offset((page - 1) * 30)
        .limit(30)
    )
    total = await db.scalar(select(func.count()).select_from(AdminOperation))
    return {"items": [model_service.operation_view(row) for row in rows], "total": total}


@router.get("/prompts")
async def prompts(db: Db) -> dict[str, Any]:
    return await prompt_service.list_prompts(db)


@router.post("/prompts", status_code=201)
async def create_prompt(payload: PromptCreate, db: Db) -> dict[str, Any]:
    return prompt_service.prompt_view(
        await prompt_service.create_prompt(db, **payload.model_dump())
    )


@router.put("/prompts/{prompt_id}")
async def update_prompt(prompt_id: str, payload: PromptUpdate, db: Db) -> dict[str, Any]:
    return prompt_service.prompt_view(
        await prompt_service.update_prompt(db, prompt_id, **payload.model_dump())
    )


@router.post("/prompts/{prompt_id}/publish")
async def publish_prompt(prompt_id: str, db: Db) -> dict[str, Any]:
    return prompt_service.prompt_view(await prompt_service.publish_prompt(db, prompt_id))


@router.delete("/prompts/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt(prompt_id: str, db: Db) -> Response:
    await prompt_service.delete_prompt(db, prompt_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/models")
async def models(db: Db) -> dict[str, Any]:
    return await model_service.model_inventory(db, get_settings())


@router.get("/speech", response_model=SpeechSettings)
async def speech_settings(db: Db) -> SpeechSettings:
    return await speech_service.read_settings(db)


@router.get("/speech/provider")
async def speech_provider() -> dict[str, Any]:
    settings = get_settings()
    return {
        "configured": is_configured(settings),
        "model": settings.tts_model,
        "default_voice": settings.tts_voice,
        "effective_default_voice": effective_voice(SpeechSettings(), settings),
        "voice_options": voice_options(settings),
        "endpoint_host": model_service.safe_host(settings.tts_base_url),
    }


@router.post("/speech/preview")
async def speech_preview(payload: SpeechPreviewRequest, db: Db) -> Response:
    settings = get_settings()
    try:
        audio = await synthesize(payload.text, payload.settings, settings)
    except Exception as exc:
        diagnostics = failure_detail(exc, "tts", settings)
        diagnostics.update(
            model=settings.tts_model, voice=effective_voice(payload.settings, settings)
        )
        prompt_service.audit(
            db, kind="speech_preview_failed", target_id="tts", status="failed", **diagnostics
        )
        await db.commit()
        raise HTTPException(502, {"code": "tts_preview_failed", **diagnostics}) from exc
    return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@router.post("/speech/probe")
async def speech_probe(payload: SpeechSettings, db: Db) -> dict[str, Any]:
    return await model_service.probe_model(db, "tts", get_settings(), speech_override=payload)


@router.put("/speech", response_model=SpeechSettings)
async def update_speech_settings(payload: SpeechSettings, db: Db) -> SpeechSettings:
    return await speech_service.save_settings(db, payload)


@router.post("/models/probe-all")
async def probe_all(db: Db) -> dict[str, Any]:
    settings = get_settings()
    results = [await model_service.probe_model(db, kind, settings) for kind in MODEL_KINDS]
    return {"items": results}


@router.post("/models/{kind}/probe")
async def probe(kind: str, db: Db) -> dict[str, Any]:
    if kind not in MODEL_KINDS:
        raise HTTPException(404, "model_kind_not_found")
    return await model_service.probe_model(db, kind, get_settings())
