import asyncio
import json

import httpx
import pytest

from medical_agent.core.config import Settings
from medical_agent.core.redis import store
from medical_agent.providers.embedding import client as embedding


@pytest.mark.asyncio
async def test_embedding_forwards_dimensions_and_preserves_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []
    original = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    def http_client(*, timeout: httpx.Timeout, limits: httpx.Limits) -> httpx.AsyncClient:
        assert limits.max_connections == 20
        return original(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(embedding.httpx, "AsyncClient", http_client)
    provider = embedding.OpenAICompatibleEmbeddingProvider(
        Settings(
            embedding_base_url="https://dashscope.example/v1",
            embedding_api_key="secret",
            embedding_model="qwen3.7-text-embedding-flash",
            embedding_dimensions=1024,
        )
    )
    assert await provider.embed(["甲", "乙"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert captured == [
        {
            "model": "qwen3.7-text-embedding-flash",
            "input": ["甲", "乙"],
            "dimensions": 1024,
        }
    ]


@pytest.mark.asyncio
async def test_embedding_cache_batches_only_unique_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache: dict[str, object] = {}

    async def get_many(keys: list[str]) -> list[object | None]:
        return [cache.get(key) for key in keys]

    async def set_many(values: dict[str, object], *, ttl_seconds: int) -> None:
        assert ttl_seconds == 60
        cache.update(values)

    monkeypatch.setattr(store, "get_many_json", get_many)
    monkeypatch.setattr(store, "set_many_json", set_many)

    class Upstream:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def embed(self, texts: list[str]) -> list[list[float]]:
            self.calls.append(texts)
            return [[float(index), 1.0] for index, _ in enumerate(texts, start=1)]

    upstream = Upstream()
    settings = Settings(
        redis_enabled=True,
        embedding_cache_ttl_seconds=60,
        embedding_base_url="https://example/v1",
        embedding_api_key="secret",
        embedding_model="model-a",
        embedding_dimensions=512,
    )
    provider = embedding.CachedEmbeddingProvider(upstream, settings)
    first = await provider.embed(["重复", "另一条", "重复"])
    second = await provider.embed(["重复", "另一条"])

    assert upstream.calls == [["重复", "另一条"]]
    assert first == [[1.0, 1.0], [2.0, 1.0], [1.0, 1.0]]
    assert second == [[1.0, 1.0], [2.0, 1.0]]


def test_embedding_fingerprint_includes_dimensions() -> None:
    base = Settings(
        embedding_base_url="https://example/v1",
        embedding_model="same-model",
    )
    assert embedding.embedding_fingerprint(base) != embedding.embedding_fingerprint(
        base.model_copy(update={"embedding_dimensions": 1024})
    )


@pytest.mark.asyncio
async def test_embedding_batcher_combines_fifty_single_queries_into_two_batches() -> None:
    class Upstream:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def embed(self, texts: list[str]) -> list[list[float]]:
            self.calls.append(texts)
            return [[float(text.removeprefix("问题-"))] for text in texts]

    upstream = Upstream()
    settings = Settings(
        embedding_query_batch_size=25,
        embedding_query_batch_wait_ms=20,
    )
    batcher = embedding.EmbeddingBatcher(upstream, settings)
    try:
        vectors = await asyncio.gather(
            *(batcher.embed([f"问题-{index}"]) for index in range(50))
        )
    finally:
        await batcher.close()

    assert [len(call) for call in upstream.calls] == [25, 25]
    assert vectors == [[[float(index)]] for index in range(50)]


@pytest.mark.asyncio
async def test_embedding_batcher_close_cancels_waiting_queries() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class Upstream:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            started.set()
            await release.wait()
            return [[1.0] for _ in texts]

    batcher = embedding.EmbeddingBatcher(
        Upstream(),
        Settings(embedding_query_batch_size=25, embedding_query_batch_wait_ms=0),
    )
    query = asyncio.create_task(batcher.embed(["尚未完成"]))
    await started.wait()
    await batcher.close()

    with pytest.raises(asyncio.CancelledError):
        await query
