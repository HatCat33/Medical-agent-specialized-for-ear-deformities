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


class PublicSpeechSettings(SpeechSettings):
    streaming_enabled: bool = False
    stream_format: Literal["pcm_s16le"] = "pcm_s16le"
    stream_sample_rate: int = Field(default=24_000, ge=8_000, le=96_000)
    stream_channels: int = Field(default=1, ge=1, le=2)


class SynthesisRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    text: str = Field(min_length=1, max_length=600)


class SpeechPreviewRequest(SynthesisRequest):
    settings: SpeechSettings


class ModelConfigurationUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid", allow_inf_nan=False)

    provider: str | None = Field(default=None, max_length=80)
    base_url: str | None = Field(default=None, max_length=2000)
    api_key: str | None = Field(default=None, max_length=8000)
    model: str | None = Field(default=None, max_length=500)
    timeout_seconds: float | None = Field(default=None, gt=0, le=300)
    max_tokens: int | None = Field(default=None, ge=1, le=100_000)
    enable_thinking: bool | None = None
    language: str | None = Field(default=None, min_length=2, max_length=35)
    batch_size: int | None = Field(default=None, ge=1, le=256)
    voice: str | None = Field(default=None, max_length=500)
    protocol: Literal[
        "openai-compatible", "azure-speech", "elevenlabs", "volcengine", "custom",
        "local-sherpa",
    ] | None = None
    streaming_enabled: bool | None = None
    retry_count: int | None = Field(default=None, ge=0, le=3)
    enabled: bool | None = None
    candidate_k: int | None = Field(default=None, ge=1, le=100)
