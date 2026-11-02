import asyncio
import io
import threading
import wave
from typing import Any

import pytest

from medical_agent.core.config import Settings
from medical_agent.modules.speech import local_tts
from medical_agent.modules.system.schemas import SpeechSettings

np = pytest.importorskip("numpy")


@pytest.fixture(autouse=True)
def admission(monkeypatch: pytest.MonkeyPatch) -> asyncio.Semaphore:
    semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(local_tts, "_admission", semaphore)
    return semaphore


class FakeModel:
    sample_rate = 24000

    def generate(self, text: str, *, sid: int, speed: float, callback: Any) -> None:
        callback(np.array([-1, 0, 1], dtype=np.float32), 1)


@pytest.mark.asyncio
async def test_local_pcm_and_preview_have_matching_sample_format(monkeypatch: pytest.MonkeyPatch) -> None:
    def load(_: Settings) -> FakeModel:
        return FakeModel()

    monkeypatch.setattr(local_tts, "_load", load)
    settings = Settings.model_construct(tts_protocol="local-sherpa")
    data = await local_tts.local_wav("测试", SpeechSettings(), settings)
    with wave.open(io.BytesIO(data), "rb") as audio:
        assert audio.getframerate() == 24000
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.readframes(3) == b"\x01\x80\x00\x00\xff\x7f"


@pytest.mark.asyncio
async def test_cancel_does_not_release_cpu_admission_until_native_worker_exits(
    monkeypatch: pytest.MonkeyPatch, admission: asyncio.Semaphore,
) -> None:
    release = threading.Event()

    class SlowModel(FakeModel):
        def generate(self, text: str, *, sid: int, speed: float, callback: Any) -> None:
            callback(np.zeros(10), 0.5)
            release.wait(timeout=5)
            callback(np.zeros(10), 1)

    def load(_: Settings) -> SlowModel:
        return SlowModel()

    monkeypatch.setattr(local_tts, "_load", load)
    iterator = local_tts.local_pcm("测试", SpeechSettings(), Settings.model_construct())
    assert len(await anext(iterator)) == 20
    await iterator.aclose()
    assert admission.locked()
    release.set()
    for _ in range(100):
        if not admission.locked():
            break
        await asyncio.sleep(0.01)
    assert not admission.locked()


@pytest.mark.asyncio
async def test_native_error_is_propagated_and_releases_admission(
    monkeypatch: pytest.MonkeyPatch, admission: asyncio.Semaphore,
) -> None:
    def failed(_: Settings) -> Any:
        raise ValueError("bad_model")

    monkeypatch.setattr(local_tts, "_load", failed)
    with pytest.raises(ValueError, match="bad_model"):
        await anext(local_tts.local_pcm("测试", SpeechSettings(), Settings.model_construct()))
    # Error delivery can precede the worker's finally callback on the event loop.
    await asyncio.wait_for(admission.acquire(), timeout=1)
    admission.release()
