from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "medical-agent"
    log_level: str = "INFO"
    log_json: bool = False
    log_file_path: str = "./logs/medical-agent.jsonl"
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 10
    interaction_log_enabled: bool = True
    interaction_log_file_path: str = "./logs/interactions.jsonl"
    interaction_log_include_content: bool = False
    interaction_log_max_content_chars: int = Field(default=20_000, ge=0, le=200_000)
    interaction_log_max_bytes: int = 20 * 1024 * 1024
    interaction_log_backup_count: int = 30
    database_url: str = "sqlite+aiosqlite:///./medical_agent.db"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:5173"
    cors_origin_regex: str = ""
    admin_api_token: str = ""

    llm_provider: str = "demo"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 60.0
    llm_max_tokens: int = 1024
    llm_enable_thinking: bool | None = None
    history_message_limit: int = 20

    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_model: str = ""
    vision_max_tokens: int = 1024
    vision_enable_thinking: bool | None = None

    asr_provider: str = "disabled"
    asr_base_url: str = ""
    asr_api_key: str = ""
    asr_model: str = ""
    asr_language: str = "zh"
    asr_timeout_seconds: float = 60.0
    asr_max_audio_bytes: int = 10 * 1024 * 1024

    tts_base_url: str = ""
    tts_api_key: str = ""
    tts_model: str = ""
    tts_voice: str = ""
    tts_timeout_seconds: float = Field(default=30, gt=0, le=120)

    media_storage_path: str = "./data/uploads"
    media_max_image_count: int = 4
    media_max_image_bytes: int = 5 * 1024 * 1024
    media_max_total_bytes: int = 16 * 1024 * 1024

    rag_enabled: bool = False
    rag_top_k: int = Field(default=4, ge=1, le=20)
    rag_min_score: float = Field(default=0.16, ge=0.0, le=1.0)
    rag_relative_score: float = Field(default=0.62, ge=0.0, le=1.0)
    rag_require_match: bool = True
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_timeout_seconds: float = Field(default=60.0, gt=0.0, le=300.0)
    embedding_batch_size: int = Field(default=32, ge=1, le=256)

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

