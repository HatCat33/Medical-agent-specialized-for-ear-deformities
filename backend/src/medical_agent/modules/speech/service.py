"""Validated ephemeral audio transcription."""

import time
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from medical_agent.core.config import Settings
from medical_agent.core.logging import get_logger
from medical_agent.providers.speech.client import SpeechProviderError, build_speech_provider
from medical_agent.providers.speech.protocol import AudioInput

logger = get_logger(__name__)

_AUDIO_FORMATS = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
}


def _detect_audio_media_type(data: bytes) -> str | None:
    if data.startswith(b"\x1aE\xdf\xa3"):
        return "audio/webm"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "audio/mp4"
    return None


async def transcribe_audio(upload: UploadFile, *, settings: Settings) -> str:
    try:
        data = await upload.read(settings.asr_max_audio_bytes + 1)
    finally:
        await upload.close()

    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="audio_empty")
    if len(data) > settings.asr_max_audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="audio_too_large",
        )
    media_type = _detect_audio_media_type(data)
    if media_type not in _AUDIO_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="audio_type_not_supported",
        )

    filename = f"recording{_AUDIO_FORMATS[media_type]}"
    original_suffix = Path(upload.filename or "").suffix.lower()
    if original_suffix == _AUDIO_FORMATS[media_type]:
        filename = Path(upload.filename or filename).name[:255]

    started_at = time.perf_counter()
    try:
        provider = build_speech_provider(settings)
        text = await provider.transcribe(
            AudioInput(filename=filename, media_type=media_type, data=data)
        )
    except SpeechProviderError as exc:
        code = str(exc)
        logger.warning(
            "speech_transcription_failed",
            reason=code,
            audio_bytes=len(data),
            duration_ms=round((time.perf_counter() - started_at) * 1000),
        )
        unavailable = code == "asr_not_configured"
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if unavailable
                else status.HTTP_502_BAD_GATEWAY
            ),
            detail=code,
        ) from exc

    logger.info(
        "speech_transcription_succeeded",
        audio_bytes=len(data),
        duration_ms=round((time.perf_counter() - started_at) * 1000),
    )
    return text
