"""Public media metadata contracts."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MediaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    original_name: str
    media_type: str
    size_bytes: int
    status: str
    created_at: datetime
