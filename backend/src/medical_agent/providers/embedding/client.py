"""OpenAI-compatible embedding provider."""

import asyncio
import hashlib
import math
import time
from dataclasses import dataclass
from typing import cast

import httpx

from medical_agent.core.config import Settings
from medical_agent.core.logging import get_logger
from medical_agent.core.redis import stable_hash, store
from medical_agent.providers.embedding.protocol import EmbeddingProvider

logger = get_logger(__name__)
_clients: dict[tuple[str, float, int], httpx.AsyncClient] = {}
_client_lock = asyncio.Lock()
_batchers: dict[str, "EmbeddingBatcher"] = {}


class EmbeddingProviderError(RuntimeError):
    pass


async def _client_for(base_url: str, timeout: float) -> httpx.AsyncClient:
    key = (base_url.rstrip("/"), timeout, id(httpx.AsyncClient))
    existing = _clients.get(key)
    if existing is not None:
        return existing
    async with _client_lock:
        existing = _clients.get(key)
        if existing is None:
            existing = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
            _clients[key] = existing
        return existing


class OpenAICompatibleEmbeddingProvider:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.embedding_base_url.rstrip("/")
        self.api_key = settings.embedding_api_key
        self.model = settings.embedding_model
        self.dimensions = settings.embedding_dimensions
        self.timeout = settings.embedding_timeout_seconds

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.base_url or not self.api_key or not self.model:
            raise EmbeddingProviderError("embedding_not_configured")
        try:
            client = await _client_for(self.base_url, self.timeout)
            payload: dict[str, object] = {"model": self.model, "input": texts}
            if self.dimensions is not None:
                payload["dimensions"] = self.dimensions
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            raw_payload = cast(object, response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingProviderError("embedding_request_failed") from exc
        try:
            if not isinstance(raw_payload, dict):
                raise TypeError("embedding_payload_invalid")
            payload = cast(dict[str, object], raw_payload)
            if not isinstance(payload.get("data"), list):
                raise TypeError("embedding_data_invalid")
            items: list[dict[str, object]] = []
            for raw_item in cast(list[object], payload["data"]):
                if not isinstance(raw_item, dict):
                    raise TypeError("embedding_item_invalid")
                items.append(cast(dict[str, object], raw_item))
            ordered = sorted(items, key=lambda item: int(cast(int | str, item["index"])))
            vectors: list[list[float]] = []
            for item in ordered:
                raw_vector = item["embedding"]
                if not isinstance(raw_vector, list):
                    raise TypeError("embedding_vector_invalid")
                values = cast(list[object], raw_vector)
                vectors.append([
                    float(cast(int | float | str, value)) for value in values
                ])
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingProviderError("embedding_response_invalid") from exc
        if len(vectors) != len(texts):
            raise EmbeddingProviderError("embedding_count_mismatch")
        return vectors


@dataclass(frozen=True)
class _BatchRequest:
    text: str
    future: asyncio.Future[list[float]]
    queued_at: float


class EmbeddingBatcher:
    """Micro-batch concurrent online queries into the provider's batch endpoint."""

    def __init__(self, provider: EmbeddingProvider, settings: Settings) -> None:
        self._provider = provider
        self._max_batch = settings.embedding_query_batch_size
        self._wait_seconds = settings.embedding_query_batch_wait_ms / 1000
        self._queue: asyncio.Queue[_BatchRequest] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._active: list[_BatchRequest] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        loop = asyncio.get_running_loop()
        requests = [
            _BatchRequest(text, loop.create_future(), time.monotonic())
            for text in texts
        ]
        for request in requests:
            self._queue.put_nowait(request)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())
        return list(await asyncio.gather(*(asyncio.shield(item.future) for item in requests)))

    async def _run(self) -> None:
        while True:
            first = await self._queue.get()
            batch = [first]
            if self._wait_seconds:
                await asyncio.sleep(self._wait_seconds)
            while len(batch) < self._max_batch:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            self._active = batch
            try:
                await self._process(batch)
            finally:
                self._active = []

    async def _process(self, batch: list[_BatchRequest]) -> None:
        unique_texts: list[str] = []
        positions: dict[str, list[_BatchRequest]] = {}
        for request in batch:
            items = positions.setdefault(request.text, [])
            items.append(request)
            if len(items) == 1:
                unique_texts.append(request.text)
        started = time.monotonic()
        try:
            vectors = await self._provider.embed(unique_texts)
            if len(vectors) != len(unique_texts):
                raise EmbeddingProviderError("embedding_count_mismatch")
        except Exception as exc:  # noqa: BLE001 - fan one provider failure out to all waiters
            for request in batch:
                if not request.future.done():
                    request.future.set_exception(exc)
            return
        for text, vector in zip(unique_texts, vectors, strict=True):
            for request in positions[text]:
                if not request.future.done():
                    request.future.set_result(vector)
        logger.info(
            "embedding_micro_batch_completed",
            requests=len(batch),
            unique_texts=len(unique_texts),
            queue_ms=round(
                (started - min(item.queued_at for item in batch)) * 1000,
                2,
            ),
            upstream_ms=round((time.monotonic() - started) * 1000, 2),
        )

    async def close(self) -> None:
        active = list(self._active)
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        for request in active:
            if not request.future.done():
                request.future.cancel()
        self._active = []
        while not self._queue.empty():
            request = self._queue.get_nowait()
            if not request.future.done():
                request.future.cancel()


async def close_embedding_clients() -> None:
    batchers = list(_batchers.values())
    _batchers.clear()
    await asyncio.gather(*(batcher.close() for batcher in batchers), return_exceptions=True)
    clients = list(_clients.values())
    _clients.clear()
    await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)


class CachedEmbeddingProvider:
    """Cache individual vectors while preserving upstream request batching."""

    def __init__(self, provider: EmbeddingProvider, settings: Settings) -> None:
        self._provider = provider
        self._ttl = settings.embedding_cache_ttl_seconds
        self._fingerprint = embedding_fingerprint(settings)

    def _key(self, text: str) -> str:
        return store.key("embedding", self._fingerprint, stable_hash(text))

    @staticmethod
    def _valid_vector(value: object) -> list[float] | None:
        if not isinstance(value, list) or not value:
            return None
        try:
            vector = [
                float(cast(int | float | str, item))
                for item in cast(list[object], value)
            ]
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(item) for item in vector):
            return None
        return vector

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        keys = [self._key(text) for text in texts]
        cached = await store.get_many_json(keys)
        results: list[list[float] | None] = [self._valid_vector(item) for item in cached]

        # Deduplicate misses within one batch. Knowledge rows often contain the
        # same normalized text after incremental imports.
        missing_texts: list[str] = []
        missing_positions: dict[str, list[int]] = {}
        for index, (text, vector) in enumerate(zip(texts, results, strict=True)):
            if vector is not None:
                continue
            positions = missing_positions.setdefault(text, [])
            positions.append(index)
            if len(positions) == 1:
                missing_texts.append(text)
        if missing_texts:
            fresh = await self._provider.embed(missing_texts)
            if len(fresh) != len(missing_texts):
                raise EmbeddingProviderError("embedding_count_mismatch")
            cache_values: dict[str, object] = {}
            for text, vector in zip(missing_texts, fresh, strict=True):
                validated = self._valid_vector(vector)
                if validated is None:
                    raise EmbeddingProviderError("embedding_response_invalid")
                for index in missing_positions[text]:
                    results[index] = validated
                cache_values[self._key(text)] = validated
            await store.set_many_json(cache_values, ttl_seconds=self._ttl)
            logger.info(
                "embedding_cache_batch",
                requested=len(texts),
                hits=len(texts) - sum(len(items) for items in missing_positions.values()),
                upstream=len(missing_texts),
            )
        return [
            vector if vector is not None else self._raise_missing_vector()
            for vector in results
        ]

    @staticmethod
    def _raise_missing_vector() -> list[float]:
        raise EmbeddingProviderError("embedding_response_invalid")


def embedding_fingerprint(settings: Settings) -> str:
    source = f"{settings.embedding_base_url.rstrip('/')}|{settings.embedding_model}"
    if settings.embedding_dimensions is not None:
        source += f"|dimensions={settings.embedding_dimensions}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def build_embedding_provider(settings: Settings) -> EmbeddingProvider | None:
    if not settings.embedding_base_url or not settings.embedding_api_key or not settings.embedding_model:
        return None
    batcher_key = stable_hash(
        embedding_fingerprint(settings),
        settings.embedding_api_key,
        settings.embedding_timeout_seconds,
        settings.embedding_query_batch_size,
        settings.embedding_query_batch_wait_ms,
    )
    batcher = _batchers.get(batcher_key)
    if batcher is None:
        batcher = EmbeddingBatcher(OpenAICompatibleEmbeddingProvider(settings), settings)
        _batchers[batcher_key] = batcher
    provider: EmbeddingProvider = batcher
    if settings.redis_enabled and settings.embedding_cache_ttl_seconds > 0:
        provider = CachedEmbeddingProvider(provider, settings)
    return provider
