"""OpenAI-compatible speech-to-text provider."""

import json
from typing import cast

import httpx

from medical_agent.core.config import Settings
from medical_agent.providers.speech.protocol import AudioInput, SpeechToTextProvider


class SpeechProviderError(RuntimeError):
    pass


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
        self.transport = transport

    async def transcribe(self, audio: AudioInput) -> str:
        if not self.base_url or not self.api_key or not self.model:
            raise SpeechProviderError("asr_not_configured")

        form = {"model": self.model, "response_format": "json"}
        if self.language:
            form["language"] = self.language
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data=form,
                    files={"file": (audio.filename, audio.data, audio.media_type)},
                )
                response.raise_for_status()
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
