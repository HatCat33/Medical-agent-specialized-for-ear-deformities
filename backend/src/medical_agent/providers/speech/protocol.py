"""Speech-to-text provider contracts."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AudioInput:
    filename: str
    media_type: str
    data: bytes


class SpeechToTextProvider(Protocol):
    async def transcribe(self, audio: AudioInput) -> str: ...

