"""Validation contracts for runtime administration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from medical_agent.modules.safety.prompt import SAFETY_SYSTEM_PROMPT


class PromptCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=50_000)
    system_content: str = Field(default=SAFETY_SYSTEM_PROMPT, min_length=1, max_length=50_000)


class PromptUpdate(PromptCreate):
    pass


class SpeechSettings(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid", allow_inf_nan=False)

    auto_play: bool = True
    provider: Literal["browser", "openai-compatible"] = "browser"
    cloud_voice: str = Field(default="", max_length=300)
    voice: str = Field(default="", max_length=300)
    language: str = Field(default="zh-CN", min_length=2, max_length=35)
    rate: float = Field(default=1, ge=0.5, le=2)
    pitch: float = Field(default=1, ge=0, le=2)
    volume: float = Field(default=1, ge=0, le=1)


class SynthesisRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    text: str = Field(min_length=1, max_length=600)


class SpeechPreviewRequest(SynthesisRequest):
    settings: SpeechSettings
