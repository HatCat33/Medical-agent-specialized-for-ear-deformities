"""Speech API contracts."""

from pydantic import BaseModel


class TranscriptionRead(BaseModel):
    text: str
