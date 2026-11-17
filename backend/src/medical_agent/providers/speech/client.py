"""OpenAI-compatible speech-to-text provider."""

import asyncio
import json
from typing import cast

import httpx

from medical_agent.core.config import Settings
from medical_agent.core.redis import capacity_slot
from medical_agent.providers.speech.protocol import AudioInput, SpeechToTextProvider

_clients: dict[tuple[str, float, int, int], httpx.AsyncClient] = {}
_client_lock = asyncio.Lock()


class SpeechProviderError(RuntimeError):
    pass


async def _client_for(
    base_url: str,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None,
) -> httpx.AsyncClient:
    key = (base_url.rstrip("/"), timeout, id(transport), id(httpx.AsyncClient))
    existing = _clients.get(key)
    if existing is not None:
        return existing
    async with _client_lock:
        existing = _clients.get(key)
        if existing is None:
            existing = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                transport=transport,
            )
            _clients[key] = existing
        return existing


async def close_speech_clients() -> None:
    clients = list(_clients.values())
    _clients.clear()
    await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)


class OpenAICompatibleSpeechProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = settings.asr_base_url.rstrip("/")
        self.api_key = settings.asr_api_key
        self.model = settings.asr_model
        self.language = settings.asr_language.strip()
        self.timeout = settings.asr_timeout_seconds
        self.settings = settings
        self.transport = transport

    async def transcribe(self, audio: AudioInput) -> str:
        if not self.base_url or not self.api_key or not self.model:
            raise SpeechProviderError("asr_not_configured")

        form = {"model": self.model, "response_format": "json"}
        if self.language:
            form["language"] = self.language
        try:
            client = await _client_for(self.base_url, self.timeout, self.transport)
            async with capacity_slot(
                self.settings,
                scope="asr-model",
                limit=self.settings.asr_concurrency_limit,
                wait_seconds=self.settings.asr_concurrency_wait_seconds,
                lease_seconds=max(30, int(self.timeout) + 30),
            ):
                response = await client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data=form,
                    files={"file": (audio.filename, audio.data, audio.media_type)},
                )
                response.raise_for_status()
        except TimeoutError as exc:
            raise SpeechProviderError("asr_capacity_exhausted") from exc
        except httpx.HTTPError as exc:
            raise SpeechProviderError("asr_request_failed") from exc

        try:
            raw_payload = cast(object, response.json())
        except (json.JSONDecodeError, ValueError) as exc:
            raise SpeechProviderError("asr_response_invalid") from exc
        payload = cast(dict[str, object], raw_payload) if isinstance(raw_payload, dict) else {}
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise SpeechProviderError("asr_response_empty")
        return text.strip()


def build_speech_provider(settings: Settings) -> SpeechToTextProvider:
    if settings.asr_provider != "openai-compatible":
        raise SpeechProviderError("asr_not_configured")
    return OpenAICompatibleSpeechProvider(settings)
