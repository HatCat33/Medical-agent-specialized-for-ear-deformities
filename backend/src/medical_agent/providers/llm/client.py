"""OpenAI-compatible and local demo chat providers."""

import asyncio
import base64
import json
from collections.abc import AsyncIterator

import httpx

from medical_agent.core.config import Settings
from medical_agent.providers.llm.protocol import ChatProvider, ChatTurn


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DemoChatProvider:
    async def complete(self, messages: list[ChatTurn]) -> str:
        return "".join([chunk async for chunk in self.stream(messages)])

    async def stream(self, messages: list[ChatTurn]) -> AsyncIterator[str]:
        user_turn = next((item for item in reversed(messages) if item.role == "user"), None)
        question = user_turn.content if user_turn else ""
        image_note = f"，并附带 {len(user_turn.images)} 张图片" if user_turn and user_turn.images else ""
        content = (
            "当前是第二版演示模式，会话、媒体历史和流式消息功能已经连通，"
            "但尚未配置支持视觉的正式模型，因此不会分析医学图片。"
            f"你本次输入的是：{question}{image_note}"
        )
        for offset in range(0, len(content), 8):
            await asyncio.sleep(0.04)
            yield content[offset : offset + 8]


class OpenAICompatibleProvider:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.llm_base_url.rstrip("/")
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model
        self.max_tokens = settings.llm_max_tokens
        self.enable_thinking = settings.llm_enable_thinking
        self.vision_base_url = settings.vision_base_url.rstrip("/")
        self.vision_api_key = settings.vision_api_key
        self.vision_model = settings.vision_model
        self.vision_max_tokens = settings.vision_max_tokens
        self.vision_enable_thinking = settings.vision_enable_thinking
        self.timeout = settings.llm_timeout_seconds

    async def complete(self, messages: list[ChatTurn]) -> str:
        content = "".join([chunk async for chunk in self.stream(messages)]).strip()
        if not content:
            raise ProviderError("model_response_empty")
        return content

    async def stream(self, messages: list[ChatTurn]) -> AsyncIterator[str]:
        has_images = any(turn.images for turn in messages)
        base_url = self.vision_base_url if has_images else self.base_url
        api_key = self.vision_api_key if has_images else self.api_key
        model = self.vision_model if has_images else self.model
        max_tokens = self.vision_max_tokens if has_images else self.max_tokens
        enable_thinking = (
            self.vision_enable_thinking if has_images else self.enable_thinking
        )
        if not base_url or not api_key or not model:
            raise ProviderError("model_not_configured")
        payload: dict[str, object] = {
            "model": model,
            "messages": [self.serialize_turn(item) for item in messages],
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if enable_thinking is not None:
            payload["enable_thinking"] = enable_thinking
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client, client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers={
                    "Accept": "text/event-stream",
                    "Authorization": f"Bearer {api_key}",
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
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                "model_request_failed",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("model_request_failed") from exc

    @staticmethod
    def serialize_turn(turn: ChatTurn) -> dict[str, object]:
        if not turn.images:
            return {"role": turn.role, "content": turn.content}
        content: list[dict[str, object]] = [{"type": "text", "text": turn.content}]
        for image in turn.images:
            encoded = base64.b64encode(image.data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{image.media_type};base64,{encoded}",
                        "detail": "auto",
                    },
                }
            )
        return {"role": turn.role, "content": content}


def build_chat_provider(settings: Settings) -> ChatProvider:
    if settings.llm_provider == "demo":
        return DemoChatProvider()
    return OpenAICompatibleProvider(settings)
