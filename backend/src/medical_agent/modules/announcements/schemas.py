"""Announcement API contracts."""

from pydantic import BaseModel, ConfigDict, Field


class AnnouncementWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=20_000)
