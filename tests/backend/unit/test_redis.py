import asyncio

import anyio
import anyio.lowlevel
import pytest

from medical_agent.core import redis as redis_module
from medical_agent.core.config import Settings
from medical_agent.core.redis import (
    RedisStore,
    capacity_slot,
    capacity_snapshot,
    reset_capacity_metrics,
    stable_hash,
)


def test_stable_hash_does_not_expose_source_text() -> None:
    result = stable_hash("患者敏感文本", "model")
    assert len(result) == 64
    assert "患者" not in result
    assert result == stable_hash("患者敏感文本", "model")


@pytest.mark.asyncio
async def test_disabled_store_fails_open_for_limits_and_idempotency() -> None:
    cache = RedisStore()
    assert await cache.allow_request(
        scope="chat", subject="visitor", limit=1, window_seconds=60,
    ) == (True, 0)
    assert await cache.claim_once("request", ttl_seconds=60) is True
    assert await cache.acquire_lease(
        scope="model", limit=1, wait_seconds=0.1, lease_seconds=30,
    ) is None


@pytest.mark.asyncio
async def test_explicit_capacity_limit_is_independent_of_global_model_limit() -> None:
    settings = Settings(model_concurrency_limit=20)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def occupy() -> None:
        async with capacity_slot(
            settings,
            scope="test-asr-capacity",
            limit=1,
            wait_seconds=0.1,
        ):
            entered.set()
            await release.wait()

    first = asyncio.create_task(occupy())
    await entered.wait()
    with pytest.raises(TimeoutError, match="model_capacity_exhausted"):
        async with capacity_slot(
            settings,
            scope="test-asr-capacity",
            limit=1,
            wait_seconds=0.01,
        ):
            pass
    release.set()
    await first


@pytest.mark.asyncio
async def test_cancelled_request_returns_local_and_distributed_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starlette cancels a scope repeatedly at every cleanup await on disconnect."""
    released: list[str] = []

    async def acquire(**_: object) -> str:
        return "test-token"

    async def release(*, scope: str, token: str | None) -> None:
        await anyio.lowlevel.checkpoint()
        assert token == "test-token"
        released.append(scope)

    monkeypatch.setattr(redis_module.store, "acquire_lease", acquire)
    monkeypatch.setattr(redis_module.store, "release_lease", release)
    settings = Settings.model_construct()
    scope = "test-cancelled-stream"
    # Eight successive disconnects must not consume all eight permits forever.
    for _ in range(8):
        with anyio.CancelScope() as cancellation:
            async with capacity_slot(settings, scope=scope, limit=8, wait_seconds=0.1):
                cancellation.cancel()
                await anyio.lowlevel.checkpoint()

    assert len(released) == 8
    async with capacity_slot(settings, scope=scope, limit=8, wait_seconds=0.1):
        pass
    assert len(released) == 9


@pytest.mark.asyncio
async def test_capacity_snapshot_tracks_active_waiting_and_rejected_requests() -> None:
    reset_capacity_metrics()
    settings = Settings(model_concurrency_limit=1, model_concurrency_wait_seconds=0.2)
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()

    async def occupy_first() -> None:
        async with capacity_slot(settings, scope="chat-model", limit=1, wait_seconds=0.2):
            first_entered.set()
            await release_first.wait()

    async def occupy_second() -> None:
        async with capacity_slot(settings, scope="chat-model", limit=1, wait_seconds=0.2):
            await release_second.wait()

    first = asyncio.create_task(occupy_first())
    await first_entered.wait()
    second = asyncio.create_task(occupy_second())
    row = next(
        item for item in capacity_snapshot(settings)["items"]
        if item["scope"] == "chat-model"
    )
    for _ in range(20):
        row = next(
            item for item in capacity_snapshot(settings)["items"]
            if item["scope"] == "chat-model"
        )
        if row["waiting"] == 1:
            break
        await asyncio.sleep(0.005)
    assert row["active"] == 1
    assert row["waiting"] == 1

    release_first.set()
    await first
    for _ in range(20):
        row = next(
            item for item in capacity_snapshot(settings)["items"]
            if item["scope"] == "chat-model"
        )
        if row["active"] == 1 and row["waiting"] == 0 and row["total_acquired"] == 2:
            break
        await asyncio.sleep(0.005)
    assert row["recent_samples"] == 2

    with pytest.raises(TimeoutError, match="model_capacity_exhausted"):
        async with capacity_slot(settings, scope="chat-model", limit=1, wait_seconds=0.01):
            pass
    rejected = next(
        item for item in capacity_snapshot(settings)["items"]
        if item["scope"] == "chat-model"
    )
    assert rejected["total_rejected"] == 1
    assert rejected["waiting"] == 0
    assert rejected["recent_samples"] == 3
    assert rejected["p95_wait_ms"] is not None

    release_second.set()
    await second
    completed = next(
        item for item in capacity_snapshot(settings)["items"]
        if item["scope"] == "chat-model"
    )
    assert completed["active"] == 0
    assert completed["waiting"] == 0
    reset_capacity_metrics()
