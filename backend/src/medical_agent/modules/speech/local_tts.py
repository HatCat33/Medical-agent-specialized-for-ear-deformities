"""CPU Chinese TTS with bounded admission; one loaded model and a cancellable PCM stream."""

import asyncio
import importlib
import io
import threading
import time
import wave
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from medical_agent.core.config import Settings
from medical_agent.core.logging import get_logger
from medical_agent.modules.system.schemas import SpeechSettings

logger = get_logger(__name__)
_lock = threading.Lock()
_model: Any = None
_model_key: tuple[str, int] | None = None
_admission = asyncio.Semaphore(1)


class LocalSynthesisStream:
    def __init__(self, first: bytes, iterator: AsyncGenerator[bytes, None]) -> None:
        self.first = first
        self.iterator = iterator

    async def iter_bytes(self) -> AsyncGenerator[bytes, None]:
        try:
            yield self.first
            async for block in self.iterator:
                yield block
        finally:
            await self.close()

    async def close(self) -> None:
        await self.iterator.aclose()


def is_local_configured(settings: Settings) -> bool:
    folder = Path(settings.tts_local_model_dir)
    return all((folder / name).is_file() for name in ("zh_CN-huayan-medium.onnx", "tokens.txt", "espeak-ng-data/phontab"))


def _load(settings: Settings) -> Any:
    global _model, _model_key
    folder = Path(settings.tts_local_model_dir).resolve()
    key = (str(folder), settings.tts_local_threads)
    if _model is not None and _model_key == key:
        return _model
    if not is_local_configured(settings):
        raise HTTPException(503, "tts_local_model_missing")
    try:
        sherpa = importlib.import_module("sherpa_onnx")
    except ImportError as exc:
        raise HTTPException(503, "tts_local_dependencies_missing") from exc
    config = sherpa.OfflineTtsConfig(
        model=sherpa.OfflineTtsModelConfig(
            vits=sherpa.OfflineTtsVitsModelConfig(
                model=str(folder / "zh_CN-huayan-medium.onnx"), tokens=str(folder / "tokens.txt"),
                data_dir=str(folder / "espeak-ng-data"),
            ),
            num_threads=settings.tts_local_threads, provider="cpu",
        ),
        max_num_sentences=1,
    )
    if not config.validate():
        raise HTTPException(503, "tts_local_model_invalid")
    _model = sherpa.OfflineTts(config)
    _model_key = key
    return _model


def _warm(settings: Settings) -> None:
    with _lock:
        model = _load(settings)
        model.generate("你好。", sid=settings.tts_local_speaker_id, speed=1)


async def warm_local_tts(settings: Settings) -> None:
    await asyncio.to_thread(_warm, settings)
    logger.info("speech_local_model_ready", threads=settings.tts_local_threads)


async def local_pcm(
    text: str, preferences: SpeechSettings, settings: Settings,
) -> AsyncGenerator[bytes, None]:
    if settings.tts_stream_channels != 1:
        raise HTTPException(422, "tts_stream_requires_mono")
    started = time.monotonic()
    try:
        await asyncio.wait_for(_admission.acquire(), settings.tts_timeout_seconds)
    except TimeoutError as exc:
        raise HTTPException(503, "tts_local_busy") from exc
    queued_ms = round((time.monotonic() - started) * 1000, 2)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()
    stopped = threading.Event()

    def produce() -> None:
        try:
            with _lock:
                if stopped.is_set():
                    return
                model = _load(settings)
                np = importlib.import_module("numpy")
                count = 0

                def callback(samples: Any, _progress: float) -> int:
                    nonlocal count
                    if stopped.is_set():
                        return 0
                    array = np.asarray(samples, dtype=np.float32)
                    if model.sample_rate != settings.tts_stream_sample_rate and len(array):
                        length = round(len(array) * settings.tts_stream_sample_rate / model.sample_rate)
                        array = np.interp(
                            np.arange(length) * model.sample_rate / settings.tts_stream_sample_rate,
                            np.arange(len(array)), array,
                        )
                    pcm = (np.clip(array, -1, 1) * 32767).astype("<i2").tobytes()
                    count += len(pcm)
                    if count > 8 * 1024 * 1024:
                        raise ValueError("tts_audio_too_large")
                    # Send small blocks; callback batches are complete sentences.
                    for offset in range(0, len(pcm), 8192):
                        loop.call_soon_threadsafe(queue.put_nowait, pcm[offset:offset + 8192])
                    return 1

                model.generate(text, sid=settings.tts_local_speaker_id, speed=preferences.rate, callback=callback)
        except Exception as exc:  # noqa: BLE001 - Forward native worker errors to the HTTP coroutine.
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)
            loop.call_soon_threadsafe(_admission.release)

    worker = asyncio.create_task(asyncio.to_thread(produce))
    first_ms: float | None = None
    audio_bytes = 0
    deadline = time.monotonic() + settings.tts_timeout_seconds
    try:
        while True:
            block = await asyncio.wait_for(queue.get(), max(0.001, deadline - time.monotonic()))
            if block is None:
                break
            if isinstance(block, Exception):
                raise block
            if first_ms is None:
                first_ms = round((time.monotonic() - started) * 1000, 2)
                logger.info("speech_stream_first_audio", protocol="local-sherpa",
                            characters=len(text), first_audio_ms=first_ms, queue_ms=queued_ms)
            audio_bytes += len(block)
            yield block
        if not audio_bytes:
            raise HTTPException(502, "tts_response_invalid")
        logger.info("speech_stream_completed", protocol="local-sherpa", characters=len(text),
                    first_audio_ms=first_ms, queue_ms=queued_ms, audio_bytes=audio_bytes,
                    upstream_ms=round((time.monotonic() - started) * 1000, 2))
    finally:
        stopped.set()
        # Native inference cannot be interrupted between callbacks. Keep admission
        # until the worker actually exits; cancelling the coroutine must not oversubscribe CPU.
        worker.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)


async def local_wav(text: str, preferences: SpeechSettings, settings: Settings) -> bytes:
    data = bytearray()
    async for block in local_pcm(text, preferences, settings):
        data.extend(block)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(settings.tts_stream_sample_rate)
        wav.writeframes(data)
    return output.getvalue()
