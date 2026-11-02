"""Cohere/Jina-compatible reranking client."""

from dataclasses import dataclass

import httpx

from medical_agent.core.config import Settings


class RerankerProviderError(RuntimeError):
    pass


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
            async with httpx.AsyncClient(timeout=self.timeout) as client:
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
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RerankerProviderError("reranker_request_failed") from exc

        rows = payload.get("results", payload.get("data")) if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise RerankerProviderError("reranker_response_invalid")
        results: list[RerankResult] = []
        try:
            for row in rows:
                index = int(row["index"])
                score = float(row.get("relevance_score", row.get("score")))
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
