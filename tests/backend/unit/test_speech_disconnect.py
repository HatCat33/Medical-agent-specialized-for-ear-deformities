"""Exercise resource ownership across real StreamingResponse disconnect paths."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack

import anyio
import anyio.lowlevel
import httpx
import pytest
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect
from starlette.types import Message, Scope

from medical_agent.core import redis as redis_module
from medical_agent.core.config import Settings
from medical_agent.core.redis import capacity_slot
from medical_agent.modules.speech.router import SynthesisStreamingResponse
from medical_agent.modules.speech.synthesis import CapacityManagedSynthesisStream, SynthesisStream


@pytest.mark.asyncio
@pytest.mark.parametrize("asgi_version", ["2.0", "2.4"])
@pytest.mark.parametrize("disconnect_at", ["http.response.start", "http.response.body"])
async def test_eight_disconnected_streams_leave_all_slots_reusable(
    monkeypatch: pytest.MonkeyPatch, asgi_version: str, disconnect_at: str,
) -> None:
    settings = Settings.model_construct()
    scope_name = f"disconnect-{asgi_version}-{disconnect_at}"
    releases: list[str] = []

    async def acquire(**_: object) -> str:
        return "lease"

    async def release(*, scope: str, token: str | None) -> None:
        await anyio.lowlevel.checkpoint()  # Actual Redis cleanup crosses a cancellation checkpoint.
        releases.append(scope)

    monkeypatch.setattr(redis_module.store, "acquire_lease", acquire)
    monkeypatch.setattr(redis_module.store, "release_lease", release)

    class Audio(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"\x01\x00" * 20
            await anyio.sleep_forever()

        async def aclose(self) -> None:
            await anyio.lowlevel.checkpoint()
            self.closed = True

    audios: list[Audio] = []
    responses: list[SynthesisStreamingResponse] = []
    for _ in range(8):
        capacity = capacity_slot(settings, scope=scope_name, limit=8, wait_seconds=0.2)
        await capacity.__aenter__()
        audio = Audio()
        audios.append(audio)
        upstream = SynthesisStream(
            httpx.Response(200, stream=audio), characters=5,
            queue_ms=0, upstream_at=time.monotonic(),
        )
        stream = CapacityManagedSynthesisStream(upstream, capacity)
        responses.append(SynthesisStreamingResponse(
            stream.iter_bytes(), background=BackgroundTask(stream.close),
        ))

    async def disconnected(response: SynthesisStreamingResponse) -> None:
        sent = asyncio.Event()

        async def receive() -> Message:
            await sent.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            if message["type"] == disconnect_at:
                sent.set()
                if asgi_version == "2.4":
                    raise OSError("client disconnected")
                await anyio.sleep_forever()

        scope: Scope = {"type": "http", "asgi": {"spec_version": asgi_version}}
        if asgi_version == "2.4":
            with pytest.raises(ClientDisconnect):
                await response(scope, receive, send)
        else:
            await response(scope, receive, send)

    async with asyncio.timeout(3):
        await asyncio.gather(*(disconnected(response) for response in responses))
    assert all(audio.closed for audio in audios)
    assert len(releases) == 8
    # Acquire all eight simultaneously; a single leaked slot causes a failure.
    async with AsyncExitStack() as stack:
        for _ in range(8):
            await stack.enter_async_context(
                capacity_slot(settings, scope=scope_name, limit=8, wait_seconds=0.2)
            )


@pytest.mark.asyncio
async def test_repeated_task_cancellation_does_not_interrupt_lease_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleaning = asyncio.Event()
    allow_release = asyncio.Event()
    released = asyncio.Event()

    async def acquire(**_: object) -> str:
        return "lease"

    async def release(**_: object) -> None:
        cleaning.set()
        await allow_release.wait()
        released.set()

    monkeypatch.setattr(redis_module.store, "acquire_lease", acquire)
    monkeypatch.setattr(redis_module.store, "release_lease", release)
    entered = asyncio.Event()
    settings = Settings.model_construct()

    async def request() -> None:
        async with capacity_slot(settings, scope="repeated-cancel", limit=1):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(request())
    await entered.wait()
    task.cancel()
    await cleaning.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    allow_release.set()
    await asyncio.wait_for(released.wait(), timeout=1)
    async with capacity_slot(settings, scope="repeated-cancel", limit=1, wait_seconds=0.2):
        pass
