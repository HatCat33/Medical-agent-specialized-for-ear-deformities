"""OpenAI-compatible embedding provider."""

import httpx

from medical_agent.core.config import Settings
from medical_agent.providers.embedding.protocol import EmbeddingProvider


class EmbeddingProviderError(RuntimeError):
    pass


class OpenAICompatibleEmbeddingProvider:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.embedding_base_url.rstrip("/")
        self.api_key = settings.embedding_api_key
        self.model = settings.embedding_model
        self.timeout = settings.embedding_timeout_seconds

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.base_url or not self.api_key or not self.model:
            raise EmbeddingProviderError("embedding_not_configured")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "input": texts},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingProviderError("embedding_request_failed") from exc
        try:
            ordered = sorted(payload["data"], key=lambda item: item["index"])
            vectors = [[float(value) for value in item["embedding"]] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingProviderError("embedding_response_invalid") from exc
        if len(vectors) != len(texts):
            raise EmbeddingProviderError("embedding_count_mismatch")
        return vectors


def build_embedding_provider(settings: Settings) -> EmbeddingProvider | None:
    if not settings.embedding_base_url or not settings.embedding_api_key or not settings.embedding_model:
        return None
    return OpenAICompatibleEmbeddingProvider(settings)
