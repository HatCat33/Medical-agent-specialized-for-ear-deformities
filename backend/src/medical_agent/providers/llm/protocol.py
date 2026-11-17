"""Chat model provider protocol."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ImageInput:
    media_type: str
    data: bytes


@dataclass(frozen=True)
class ChatTurn:
    role: str
    content: str
    images: tuple[ImageInput, ...] = ()


class ChatProvider(Protocol):
    async def complete(self, messages: list[ChatTurn]) -> str: ...

    def stream(self, messages: list[ChatTurn]) -> AsyncIterator[str]: ...

