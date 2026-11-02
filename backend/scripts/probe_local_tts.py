"""Check local TTS timing and optionally transcribe synthetic audio to verify intelligibility."""

import argparse
import asyncio
import io
import json
import time
import wave

from medical_agent.core.config import get_settings
from medical_agent.modules.speech.local_tts import local_pcm, warm_local_tts
from medical_agent.modules.speech.service import transcribe_audio_bytes
from medical_agent.modules.system.schemas import SpeechSettings


async def main(check_asr: bool) -> None:
    settings = get_settings()
    await warm_local_tts(settings)
    text = "耳廓护理需要保持清洁。如果出现红肿或渗液，建议联系医生。"
    start = time.monotonic()
    first_ms = None
    pcm = bytearray()
    async for block in local_pcm(text, SpeechSettings(), settings):
        if first_ms is None:
            first_ms = round((time.monotonic() - start) * 1000)
        pcm.extend(block)
    report: dict[str, object] = {
        "first_audio_ms": first_ms, "total_ms": round((time.monotonic() - start) * 1000),
        "audio_seconds": round(len(pcm) / 2 / settings.tts_stream_sample_rate, 2),
    }
    if check_asr:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(settings.tts_stream_sample_rate)
            wav.writeframes(pcm)
        report["test_text"] = text
        report["transcription"] = await transcribe_audio_bytes(
            buffer.getvalue(), filename="synthetic-test.wav", media_type="audio/wav", settings=settings,
        )
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-asr", action="store_true", help="Calls your configured ASR service")
    asyncio.run(main(parser.parse_args().check_asr))
