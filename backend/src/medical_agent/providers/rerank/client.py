"""Cohere/Jina-compatible reranking client."""

import asyncio
from dataclasses import dataclass
from typing import cast

import httpx

from medical_agent.core.config import Settings
from medical_agent.core.redis import capacity_slot

_clients: dict[tuple[str, float, int], httpx.AsyncClient] = {}
_client_lock = asyncio.Lock()


class RerankerProviderError(RuntimeError):
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


async def close_reranker_clients() -> None:
    clients = list(_clients.values())
    _clients.clear()
    await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)


@dataclass(frozen=True)
class RerankResult:
    index: int
    score: float


class CompatibleRerankerProvider:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.reranker_base_url.rstrip("/")
        self.api_key = settings.reranker_api_key
        self.model = settings.reranker_model
        self.timeout = settings.reranker_timeout_seconds
        self.settings = settings

    async def rerank(
        self,
        *,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[RerankResult]:
        if not self.base_url or not self.api_key or not self.model:
            raise RerankerProviderError("reranker_not_configured")
        endpoint = self.base_url if self.base_url.endswith("/rerank") else f"{self.base_url}/rerank"
        try:
            client = await _client_for(self.base_url, self.timeout)
            async with capacity_slot(
                self.settings,
                scope="reranker-model",
                limit=self.settings.reranker_concurrency_limit,
                wait_seconds=self.settings.reranker_concurrency_wait_seconds,
                lease_seconds=max(30, int(self.timeout) + 30),
            ):
                response = await client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "query": query,
                        "documents": documents,
                        "top_n": min(top_n, len(documents)),
                        "return_documents": False,
                    },
                )
                response.raise_for_status()
                payload: object = response.json()
        except TimeoutError as exc:
            raise RerankerProviderError("reranker_capacity_exhausted") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise RerankerProviderError("reranker_request_failed") from exc

        if not isinstance(payload, dict):
            raise RerankerProviderError("reranker_response_invalid")
        payload_fields = cast(dict[str, object], payload)
        rows = payload_fields.get("results", payload_fields.get("data"))
        if not isinstance(rows, list):
            raise RerankerProviderError("reranker_response_invalid")
        results: list[RerankResult] = []
        try:
            for item in cast(list[object], rows):
                if not isinstance(item, dict):
                    raise TypeError("result_not_an_object")
                row = cast(dict[str, object], item)
                index_value = row["index"]
                score_value = row.get("relevance_score", row.get("score"))
                if not isinstance(index_value, (int, str)) or not isinstance(
                    score_value, (int, float, str)
                ):
                    raise TypeError("result_value_invalid")
                index = int(index_value)
                score = float(score_value)
                if not 0 <= index < len(documents):
                    raise ValueError("index_out_of_range")
                results.append(RerankResult(index=index, score=score))
        except (KeyError, TypeError, ValueError) as exc:
            raise RerankerProviderError("reranker_response_invalid") from exc
        return results


def build_reranker_provider(settings: Settings) -> CompatibleRerankerProvider | None:
    if not settings.reranker_enabled:
        return None
    if not (
        settings.reranker_base_url
        and settings.reranker_api_key
        and settings.reranker_model
    ):
        return None
    return CompatibleRerankerProvider(settings)
