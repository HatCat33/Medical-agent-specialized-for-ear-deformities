"""Chat model provider protocol."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChatTurn:
    role: str
    content: str


class ChatProvider(Protocol):
    async def complete(self, messages: list[ChatTurn]) -> str: ...

    def stream(self, messages: list[ChatTurn]) -> AsyncIterator[str]: ...

