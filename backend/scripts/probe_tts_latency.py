"""Measure real first audio latency with synthetic text; never print credentials or audio."""

import asyncio
import json
import time

import httpx

from medical_agent.core.config import get_settings
from medical_agent.core.database import session_factory
from medical_agent.modules.speech.synthesis import effective_voice
from medical_agent.modules.system.model_config_service import load_runtime_model_configuration
from medical_agent.modules.system.speech_service import read_settings


async def main() -> None:
    async with session_factory() as db:
        await load_runtime_model_configuration(db)
        preferences = await read_settings(db)
    settings = get_settings()
    async with httpx.AsyncClient(timeout=35) as client:
        for extra in ({}, {"stream": True, "sample_rate": 24000}):
            payload = {
                "model": settings.tts_model,
                "voice": effective_voice(preferences, settings),
                "input": "这是一次语音播放测试，请保持自然的语速。",
                "response_format": "pcm", "speed": 1, **extra,
            }
            start = time.monotonic()
            try:
                async with client.stream(
                    "POST", settings.tts_base_url.rstrip("/") + "/audio/speech",
                    headers={"Authorization": "Bearer " + settings.tts_api_key}, json=payload,
                ) as response:
                    headers_ms = round((time.monotonic() - start) * 1000)
                    sizes: list[int] = []
                    times: list[int] = []
                    signature = ""
                    async for block in response.aiter_bytes():
                        if not block:
                            continue
                        if not sizes:
                            signature = block[:12].hex()
                        sizes.append(len(block))
                        times.append(round((time.monotonic() - start) * 1000))
                    print(json.dumps({
                        "model": settings.tts_model, "extra": extra, "status": response.status_code,
                        "type": response.headers.get("content-type"), "headers_ms": headers_ms,
                        "first_ms": times[0] if times else None, "last_ms": times[-1] if times else None,
                        "chunks": len(sizes), "bytes": sum(sizes), "first_12_hex": signature,
                    }), flush=True)
            except httpx.HTTPError as exc:
                print(json.dumps({"error_type": type(exc).__name__, "extra": extra}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
