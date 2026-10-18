"""Ephemeral speech transcription endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from medical_agent.api.dependencies import current_owner_id
from medical_agent.core.config import get_settings
from medical_agent.modules.speech.schemas import TranscriptionRead
from medical_agent.modules.speech.service import transcribe_audio

router = APIRouter(prefix="/speech", tags=["speech"])
OwnerId = Annotated[str, Depends(current_owner_id)]


@router.post("/transcriptions", response_model=TranscriptionRead)
async def create_transcription(
    audio: Annotated[UploadFile, File()],
    owner_id: OwnerId,
) -> TranscriptionRead:
    del owner_id  # Authentication is required; raw audio is deliberately not persisted.
    text = await transcribe_audio(audio, settings=get_settings())
    return TranscriptionRead(text=text)
