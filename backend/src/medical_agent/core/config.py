from functools import lru_cache

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
    database_url: str = "sqlite+aiosqlite:///./medical_agent.db"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:5173"
    cors_origin_regex: str = ""

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

    media_storage_path: str = "./data/uploads"
    media_max_image_count: int = 4
    media_max_image_bytes: int = 5 * 1024 * 1024
    media_max_total_bytes: int = 16 * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

