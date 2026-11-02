"""Loopback-only TTS verification server. No bot connections or patient conversation writes."""

import argparse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from medical_agent.core.config import get_settings
from medical_agent.core.database import close_db, session_factory
from medical_agent.modules.speech.synthesis import close_synthesis_clients, open_synthesis_stream
from medical_agent.modules.system.model_config_service import load_runtime_model_configuration
from medical_agent.modules.system.schemas import SpeechSettings, SynthesisRequest
from medical_agent.modules.system.speech_service import read_settings

preferences = SpeechSettings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global preferences
    async with session_factory() as db:
        await load_runtime_model_configuration(db)
        preferences = await read_settings(db)
    if get_settings().tts_protocol == "local-sherpa":
        from medical_agent.modules.speech.local_tts import warm_local_tts

        await warm_local_tts(get_settings())
    yield
    await close_synthesis_clients()
    await close_db()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origin_regex=r"https?://(127\.0\.0\.1|localhost)(:\d+)?",
    allow_methods=["POST", "GET"], allow_headers=["Content-Type", "X-Visitor-ID"],
)


@app.get("/settings")
async def settings() -> dict[str, object]:
    value = get_settings()
    return {
        **preferences.model_dump(), "provider": "openai-compatible", "streaming_enabled": True,
        "stream_sample_rate": value.tts_stream_sample_rate,
        "stream_channels": value.tts_stream_channels,
    }


@app.post("/stream")
async def stream(payload: SynthesisRequest) -> StreamingResponse:
    audio = await open_synthesis_stream(payload.text, preferences, get_settings())
    return StreamingResponse(
        audio.iter_bytes(), media_type="application/octet-stream",
        background=BackgroundTask(audio.close),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18669)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, ws="none", log_level="warning")
