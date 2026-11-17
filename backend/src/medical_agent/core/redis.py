"""Fail-open Redis cache, rate-limit, and distributed lease helpers.

Redis is an optimization and coordination layer. PostgreSQL remains the source
of truth, so an unavailable Redis instance must not make ordinary reads or
writes unavailable. Capacity controls deliberately fail open and emit a
structured warning; upstream timeouts remain the final protection.
"""

import asyncio
import hashlib
import json
import os
import time
import uuid
from collections import deque
from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypedDict, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from medical_agent.core.cleanup import complete_cleanup
from medical_agent.core.config import Settings
from medical_agent.core.logging import get_logger

logger = get_logger(__name__)

_ACQUIRE_LEASE = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local expires = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local token = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
if redis.call('ZCARD', key) >= limit then
  return 0
end
redis.call('ZADD', key, expires, token)
redis.call('EXPIRE', key, math.max(1, math.ceil((expires - now) / 1000)))
return 1
"""
_DELETE_IF_VALUE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def stable_hash(*parts: object) -> str:
    """Hash cache-key material so patient text and credentials never appear in keys."""
    source = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


class RedisStore:
    def __init__(self) -> None:
        self._client: Redis | None = None
        self._prefix = "medical-agent"
        self._enabled = False
        self._last_failure_log = 0.0

    @property
    def available(self) -> bool:
        return self._client is not None

    async def start(self, settings: Settings) -> None:
        await self.close()
        self._enabled = settings.redis_enabled
        self._prefix = settings.redis_key_prefix.strip().strip(":") or "medical-agent"
        if not self._enabled:
            logger.info("redis_disabled")
            return
        client = Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
            settings.redis_url,
            decode_responses=False,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            socket_timeout=settings.redis_operation_timeout_seconds,
            health_check_interval=30,
        )
        try:
            await cast(
                Awaitable[object],
                client.ping(),  # pyright: ignore[reportUnknownMemberType]
            )
        except (RedisError, OSError, TimeoutError) as exc:
            await client.aclose()
            self._log_failure("redis_start_failed", exc)
            return
        self._client = client
        logger.info("redis_connected", key_prefix=self._prefix)

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    def key(self, namespace: str, *parts: object) -> str:
        suffix = ":".join(str(part).replace(":", "_") for part in parts)
        return f"{self._prefix}:{namespace}:{suffix}" if suffix else f"{self._prefix}:{namespace}"

    async def get_bytes(self, key: str) -> bytes | None:
        client = self._client
        if client is None:
            return None
        try:
            value = await client.get(key)
        except (RedisError, OSError, TimeoutError) as exc:
            self._log_failure("redis_get_failed", exc)
            return None
        if isinstance(value, bytes):
            return value
        if isinstance(value, bytearray):
            return bytes(value)
        if isinstance(value, memoryview):
            return value.tobytes()
        return None

    async def set_bytes(self, key: str, value: bytes, *, ttl_seconds: int) -> bool:
        client = self._client
        if client is None or ttl_seconds <= 0:
            return False
        try:
            await client.set(key, value, ex=ttl_seconds)
        except (RedisError, OSError, TimeoutError) as exc:
            self._log_failure("redis_set_failed", exc)
            return False
        return True

    async def delete(self, key: str) -> None:
        client = self._client
        if client is None:
            return
        try:
            await client.delete(key)
        except (RedisError, OSError, TimeoutError) as exc:
            self._log_failure("redis_delete_failed", exc)

    async def exists(self, key: str) -> bool:
        client = self._client
        if client is None:
            return False
        try:
            return bool(await client.exists(key))
        except (RedisError, OSError, TimeoutError) as exc:
            self._log_failure("redis_exists_failed", exc)
            return False

    async def claim_once(self, key: str, *, ttl_seconds: int) -> bool:
        """Claim an idempotency key; fail open when Redis is unavailable."""
        client = self._client
        if client is None or ttl_seconds <= 0:
            return True
        try:
            return bool(await client.set(key, b"1", ex=ttl_seconds, nx=True))
        except (RedisError, OSError, TimeoutError) as exc:
            self._log_failure("redis_claim_failed", exc)
            return True

    async def delete_if_value(self, key: str, value: str) -> None:
        client = self._client
        if client is None:
            return
        try:
            await cast(Awaitable[object], client.eval(_DELETE_IF_VALUE, 1, key, value))
        except (RedisError, OSError, TimeoutError) as exc:
            self._log_failure("redis_conditional_delete_failed", exc)

    async def get_json(self, key: str) -> Any | None:
        value = await self.get_bytes(key)
        if value is None:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return None

    async def set_json(self, key: str, value: object, *, ttl_seconds: int) -> bool:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            return False
        return await self.set_bytes(key, encoded, ttl_seconds=ttl_seconds)

    async def get_many_json(self, keys: list[str]) -> list[Any | None]:
        client = self._client
        if client is None or not keys:
            return [None] * len(keys)
        try:
            values = await client.mget(keys)
        except (RedisError, OSError, TimeoutError) as exc:
            self._log_failure("redis_mget_failed", exc)
            return [None] * len(keys)
        decoded: list[Any | None] = []
        for value in values:
            if isinstance(value, bytes):
                raw = value
            elif isinstance(value, bytearray):
                raw = bytes(value)
            elif isinstance(value, memoryview):
                raw = value.tobytes()
            else:
                decoded.append(None)
                continue
            try:
                decoded.append(json.loads(raw))
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                decoded.append(None)
        return decoded

    async def set_many_json(
        self,
        values: dict[str, object],
        *,
        ttl_seconds: int,
    ) -> None:
        client = self._client
        if client is None or not values or ttl_seconds <= 0:
            return
        try:
            async with client.pipeline(transaction=False) as pipeline:
                for key, value in values.items():
                    encoded = json.dumps(
                        value,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                    pipeline.set(key, encoded, ex=ttl_seconds)
                await pipeline.execute()
        except (RedisError, OSError, TimeoutError, TypeError, ValueError) as exc:
            self._log_failure("redis_mset_failed", exc)

    async def allow_request(
        self,
        *,
        scope: str,
        subject: str,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        """Return (allowed, retry_after); fail open when Redis is unavailable."""
        client = self._client
        if client is None or limit <= 0:
            return True, 0
        bucket = int(time.time()) // window_seconds
        key = self.key("rate", scope, stable_hash(subject), bucket)
        try:
            async with client.pipeline(transaction=True) as pipeline:
                pipeline.incr(key)
                pipeline.expire(key, window_seconds + 1)
                count, _ = await pipeline.execute()
        except (RedisError, OSError, TimeoutError) as exc:
            self._log_failure("redis_rate_limit_failed", exc)
            return True, 0
        retry_after = window_seconds - (int(time.time()) % window_seconds)
        return int(cast(int, count)) <= limit, max(1, retry_after)

    async def acquire_lease(
        self,
        *,
        scope: str,
        limit: int,
        wait_seconds: float,
        lease_seconds: int,
    ) -> str | None:
        """Acquire one distributed capacity slot, or return an empty token on timeout.

        None means Redis is disabled/unavailable and callers should use their
        in-process limit. An empty string means Redis was healthy but full.
        """
        client = self._client
        if client is None or limit <= 0:
            return None
        token = uuid.uuid4().hex
        key = self.key("lease", scope)
        deadline = time.monotonic() + wait_seconds
        while True:
            now_ms = int(time.time() * 1000)
            try:
                acquired = await cast(
                    Awaitable[object],
                    client.eval(
                        _ACQUIRE_LEASE,
                        1,
                        key,
                        str(now_ms),
                        str(now_ms + lease_seconds * 1000),
                        str(limit),
                        token,
                    ),
                )
            except (RedisError, OSError, TimeoutError) as exc:
                self._log_failure("redis_lease_failed", exc)
                return None
            if int(cast(int, acquired)) == 1:
                return token
            if time.monotonic() >= deadline:
                return ""
            await asyncio.sleep(0.05)

    async def release_lease(self, *, scope: str, token: str | None) -> None:
        client = self._client
        if client is None or not token:
            return
        try:
            await client.zrem(self.key("lease", scope), token)
        except (RedisError, OSError, TimeoutError) as exc:
            self._log_failure("redis_lease_release_failed", exc)

    def _log_failure(self, event: str, exc: BaseException) -> None:
        now = time.monotonic()
        if now - self._last_failure_log < 30:
            return
        self._last_failure_log = now
        logger.warning(event, error_type=type(exc).__name__)


store = RedisStore()
_local_semaphores: dict[tuple[str, int], asyncio.Semaphore] = {}

_CAPACITY_WINDOW_SECONDS = 300
_CAPACITY_SCOPES = {
    "chat-model": ("对话模型", "model_concurrency_limit", "model_concurrency_wait_seconds"),
    "vision-model": ("视觉模型", "vision_concurrency_limit", "vision_concurrency_wait_seconds"),
    "asr-model": ("语音识别", "asr_concurrency_limit", "asr_concurrency_wait_seconds"),
    "tts-model": ("语音合成", "tts_concurrency_limit", "tts_concurrency_wait_seconds"),
    "reranker-model": (
        "结果精排",
        "reranker_concurrency_limit",
        "reranker_concurrency_wait_seconds",
    ),
}


@dataclass
class _CapacityMetrics:
    waiting: int = 0
    active: int = 0
    total_acquired: int = 0
    total_rejected: int = 0
    last_wait_ms: float | None = None
    last_activity_at: datetime | None = None
    waits: deque[tuple[float, float]] = field(
        default_factory=lambda: deque[tuple[float, float]]()
    )


_capacity_metrics: dict[str, _CapacityMetrics] = {}


class CapacityMetricView(TypedDict):
    scope: str
    label: str
    limit: int
    wait_seconds: float
    active: int
    waiting: int
    utilization_percent: float | None
    total_acquired: int
    total_rejected: int
    recent_samples: int
    average_wait_ms: float | None
    p50_wait_ms: float | None
    p95_wait_ms: float | None
    max_wait_ms: float | None
    last_wait_ms: float | None
    last_activity_at: datetime | None


class CapacitySnapshot(TypedDict):
    items: list[CapacityMetricView]
    sampled_at: datetime
    window_seconds: int
    process_id: int
    redis_connected: bool


def _metrics_for(scope: str) -> _CapacityMetrics:
    return _capacity_metrics.setdefault(scope, _CapacityMetrics())


def _record_wait(metrics: _CapacityMetrics, wait_ms: float) -> None:
    now = time.monotonic()
    metrics.waits.append((now, wait_ms))
    metrics.last_wait_ms = wait_ms
    metrics.last_activity_at = datetime.now(UTC)
    cutoff = now - _CAPACITY_WINDOW_SECONDS
    while metrics.waits and metrics.waits[0][0] < cutoff:
        metrics.waits.popleft()


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile + 0.9999)))
    return round(ordered[index], 2)


def capacity_snapshot(settings: Settings) -> CapacitySnapshot:
    """Return process-local admission pressure without exposing request content."""
    now = time.monotonic()
    cutoff = now - _CAPACITY_WINDOW_SECONDS
    items: list[CapacityMetricView] = []
    for scope, (label, limit_field, wait_field) in _CAPACITY_SCOPES.items():
        metrics = _metrics_for(scope)
        while metrics.waits and metrics.waits[0][0] < cutoff:
            metrics.waits.popleft()
        values = [wait_ms for _, wait_ms in metrics.waits]
        limit = int(getattr(settings, limit_field))
        items.append(
            {
                "scope": scope,
                "label": label,
                "limit": limit,
                "wait_seconds": float(getattr(settings, wait_field)),
                "active": metrics.active,
                "waiting": metrics.waiting,
                "utilization_percent": (
                    round(metrics.active / limit * 100, 1) if limit > 0 else None
                ),
                "total_acquired": metrics.total_acquired,
                "total_rejected": metrics.total_rejected,
                "recent_samples": len(values),
                "average_wait_ms": round(sum(values) / len(values), 2) if values else None,
                "p50_wait_ms": _percentile(values, 0.50),
                "p95_wait_ms": _percentile(values, 0.95),
                "max_wait_ms": round(max(values), 2) if values else None,
                "last_wait_ms": metrics.last_wait_ms,
                "last_activity_at": metrics.last_activity_at,
            }
        )
    return {
        "items": items,
        "sampled_at": datetime.now(UTC),
        "window_seconds": _CAPACITY_WINDOW_SECONDS,
        "process_id": os.getpid(),
        "redis_connected": store.available,
    }


def reset_capacity_metrics() -> None:
    """Reset process counters for isolated tests."""
    _capacity_metrics.clear()


async def init_redis(settings: Settings) -> None:
    await store.start(settings)


async def close_redis() -> None:
    await store.close()


@asynccontextmanager
async def capacity_slot(
    settings: Settings,
    *,
    scope: str,
    limit: int | None = None,
    wait_seconds: float | None = None,
    lease_seconds: int | None = None,
) -> AsyncGenerator[None, None]:
    """Bound one upstream capability locally and across API workers.

    Explicit limits let chat, vision, ASR, reranking, and TTS queue independently.
    This prevents one slow supplier endpoint from consuming every application task.
    """
    limit = settings.model_concurrency_limit if limit is None else limit
    wait_seconds = (
        settings.model_concurrency_wait_seconds if wait_seconds is None else wait_seconds
    )
    lease_seconds = (
        settings.model_concurrency_lease_seconds if lease_seconds is None else lease_seconds
    )
    if limit <= 0:
        yield
        return
    queued_at = time.monotonic()
    metrics = _metrics_for(scope)
    metrics.waiting += 1
    metrics.last_activity_at = datetime.now(UTC)
    semaphore = _local_semaphores.setdefault((scope, limit), asyncio.Semaphore(limit))
    local_acquired = False
    admitted = False
    token: str | None = None
    try:
        try:
            await asyncio.wait_for(
                semaphore.acquire(),
                timeout=wait_seconds,
            )
            local_acquired = True
        except TimeoutError as exc:
            wait_ms = round((time.monotonic() - queued_at) * 1000, 2)
            metrics.total_rejected += 1
            _record_wait(metrics, wait_ms)
            logger.warning(
                "model_capacity_rejected",
                scope=scope,
                layer="process",
                limit=limit,
                queue_ms=wait_ms,
            )
            raise TimeoutError("model_capacity_exhausted") from exc
        token = await store.acquire_lease(
            scope=scope,
            limit=limit,
            wait_seconds=wait_seconds,
            lease_seconds=lease_seconds,
        )
        if token == "":
            wait_ms = round((time.monotonic() - queued_at) * 1000, 2)
            metrics.total_rejected += 1
            _record_wait(metrics, wait_ms)
            logger.warning(
                "model_capacity_rejected",
                scope=scope,
                layer="redis",
                limit=limit,
                queue_ms=wait_ms,
            )
            raise TimeoutError("model_capacity_exhausted")
        queue_ms = round((time.monotonic() - queued_at) * 1000, 2)
        metrics.waiting = max(0, metrics.waiting - 1)
        metrics.active += 1
        metrics.total_acquired += 1
        admitted = True
        _record_wait(metrics, queue_ms)
        if queue_ms >= 100:
            logger.info(
                "model_capacity_acquired",
                scope=scope,
                limit=limit,
                queue_ms=queue_ms,
                distributed=token is not None,
            )
        yield
    finally:
        if not admitted:
            metrics.waiting = max(0, metrics.waiting - 1)
        else:
            metrics.active = max(0, metrics.active - 1)
            metrics.last_activity_at = datetime.now(UTC)
        if local_acquired:
            # No await before local release: a cancelled HTTP stream must never pin
            # this process's permit while Redis cleanup is interrupted or unavailable.
            semaphore.release()
            await complete_cleanup(store.release_lease(scope=scope, token=token))

