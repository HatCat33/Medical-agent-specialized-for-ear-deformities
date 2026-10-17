"""OpenAI-compatible and local demo chat providers."""

import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from medical_agent.core.config import Settings
from medical_agent.providers.llm.protocol import ChatProvider, ChatTurn


class ProviderError(RuntimeError):
    pass


class DemoChatProvider:
    async def complete(self, messages: list[ChatTurn]) -> str:
        return "".join([chunk async for chunk in self.stream(messages)])

    async def stream(self, messages: list[ChatTurn]) -> AsyncIterator[str]:
        question = next((item.content for item in reversed(messages) if item.role == "user"), "")
        content = (
            "当前是第一版演示模式，页面、会话和消息保存功能已经连通，"
            "但尚未配置正式大模型，因此不会生成医学内容。"
            f"你本次输入的是：{question}"
        )
        for offset in range(0, len(content), 8):
            await asyncio.sleep(0.04)
            yield content[offset : offset + 8]


class OpenAICompatibleProvider:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.llm_base_url.rstrip("/")
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model
        self.timeout = settings.llm_timeout_seconds

    async def complete(self, messages: list[ChatTurn]) -> str:
        content = "".join([chunk async for chunk in self.stream(messages)]).strip()
        if not content:
            raise ProviderError("model_response_empty")
        return content

    async def stream(self, messages: list[ChatTurn]) -> AsyncIterator[str]:
        if not self.base_url or not self.api_key or not self.model:
            raise ProviderError("model_not_configured")
        payload = {
            "model": self.model,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "temperature": 0.2,
            "stream": True,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client, client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Accept": "text/event-stream",
                    "Authorization": f"Bearer {self.api_key}",
                },
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    raw_data = line.removeprefix("data:").strip()
                    if raw_data == "[DONE]":
                        break
                    try:
                        data = json.loads(raw_data)
                        choice = data["choices"][0]
                        content = choice.get("delta", {}).get("content")
                        if content is None:
                            content = choice.get("message", {}).get("content")
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                        raise ProviderError("model_response_invalid") from exc
                    if isinstance(content, str) and content:
                        yield content
        except httpx.HTTPError as exc:
            raise ProviderError("model_request_failed") from exc


def build_chat_provider(settings: Settings) -> ChatProvider:
    if settings.llm_provider == "demo":
        return DemoChatProvider()
    return OpenAICompatibleProvider(settings)
