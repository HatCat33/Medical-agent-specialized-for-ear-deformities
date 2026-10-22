"""Ephemeral speech transcription endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.api.dependencies import current_owner_id
from medical_agent.core.config import get_settings
from medical_agent.core.database import get_db
from medical_agent.modules.speech.schemas import TranscriptionRead
from medical_agent.modules.speech.service import transcribe_audio
from medical_agent.modules.speech.synthesis import synthesize
from medical_agent.modules.system.schemas import SpeechSettings, SynthesisRequest
from medical_agent.modules.system.speech_service import read_settings

router = APIRouter(prefix="/speech", tags=["speech"])
OwnerId = Annotated[str, Depends(current_owner_id)]


@router.get("/settings", response_model=SpeechSettings)
async def public_speech_settings(db: Annotated[AsyncSession, Depends(get_db)]) -> SpeechSettings:
    return await read_settings(db)


@router.post("/syntheses")
async def create_synthesis(
    payload: SynthesisRequest,
    owner_id: OwnerId,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_visitor_id: Annotated[str | None, Header()] = None,
) -> Response:
    if not x_visitor_id or not x_visitor_id.strip():
        raise HTTPException(401, "visitor_required")
    del owner_id
    preferences = await read_settings(db)
    audio = await synthesize(payload.text, preferences, get_settings())
    return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@router.post("/transcriptions", response_model=TranscriptionRead)
async def create_transcription(
    audio: Annotated[UploadFile, File()],
    owner_id: OwnerId,
) -> TranscriptionRead:
    del owner_id  # Authentication is required; raw audio is deliberately not persisted.
    text = await transcribe_audio(audio, settings=get_settings())
    return TranscriptionRead(text=text)
