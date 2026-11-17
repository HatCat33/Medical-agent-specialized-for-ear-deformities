"""Ephemeral speech transcription endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask
from starlette.types import Receive, Scope, Send

from medical_agent.api.dependencies import current_owner_id
from medical_agent.core.cleanup import complete_cleanup
from medical_agent.core.config import get_settings
from medical_agent.core.database import get_db
from medical_agent.modules.speech.schemas import TranscriptionRead
from medical_agent.modules.speech.service import transcribe_audio
from medical_agent.modules.speech.synthesis import open_synthesis_stream, synthesize
from medical_agent.modules.system.schemas import PublicSpeechSettings, SynthesisRequest
from medical_agent.modules.system.speech_service import read_settings_cached

router = APIRouter(prefix="/speech", tags=["speech"])
OwnerId = Annotated[str, Depends(current_owner_id)]


class SynthesisStreamingResponse(StreamingResponse):
    """Release the pre-opened TTS stream even when sending headers/body fails.

    Starlette's normal background callback runs only after successful streaming.
    Taking ownership here covers disconnects before the first iterator read too.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        cleanup = self.background
        self.background = None
        try:
            await super().__call__(scope, receive, send)
        finally:
            if cleanup is not None:
                await complete_cleanup(cleanup())


@router.get("/settings", response_model=PublicSpeechSettings)
async def public_speech_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PublicSpeechSettings:
    preferences = await read_settings_cached(db)
    settings = get_settings()
    return PublicSpeechSettings(
        **preferences.model_dump(),
        streaming_enabled=(
            preferences.provider == "openai-compatible" and settings.tts_streaming_enabled
        ),
        stream_sample_rate=settings.tts_stream_sample_rate,
        stream_channels=settings.tts_stream_channels,
    )


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
    preferences = await read_settings_cached(db)
    # Synthesis can outlive an ordinary request by tens of seconds. The setting
    # snapshot is complete, so return the database connection before cloud I/O.
    await db.close()
    audio = await synthesize(payload.text, preferences, get_settings())
    return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@router.post("/syntheses/stream")
async def create_streaming_synthesis(
    payload: SynthesisRequest,
    owner_id: OwnerId,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_visitor_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    if not x_visitor_id or not x_visitor_id.strip():
        raise HTTPException(401, "visitor_required")
    del owner_id
    settings = get_settings()
    preferences = await read_settings_cached(db)
    # StreamingResponse keeps yield dependencies alive until audio completes.
    # Close explicitly so each TTS segment does not pin a PostgreSQL connection.
    await db.close()
    stream = await open_synthesis_stream(payload.text, preferences, settings)
    return SynthesisStreamingResponse(
        stream.iter_bytes(),
        media_type="application/octet-stream",
        background=BackgroundTask(stream.close),
        headers={
            "Cache-Control": "no-store, no-transform",
            "X-Accel-Buffering": "no",
            "X-Audio-Format": "pcm_s16le",
            "X-Audio-Sample-Rate": str(settings.tts_stream_sample_rate),
            "X-Audio-Channels": str(settings.tts_stream_channels),
        },
    )


@router.post("/transcriptions", response_model=TranscriptionRead)
async def create_transcription(
    audio: Annotated[UploadFile, File()],
    owner_id: OwnerId,
) -> TranscriptionRead:
    del owner_id  # Authentication is required; raw audio is deliberately not persisted.
    text = await transcribe_audio(audio, settings=get_settings())
    return TranscriptionRead(text=text)
