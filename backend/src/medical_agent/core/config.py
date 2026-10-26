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
    history_message_limit: int = Field(default=20, ge=2, le=200)
    history_summary_batch_size: int = Field(default=8, ge=2, le=100)
    history_summary_max_chars: int = Field(default=3000, ge=500, le=20_000)
    history_image_limit: int = Field(default=4, ge=0, le=12)
    history_image_max_total_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=0,
        le=64 * 1024 * 1024,
    )

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

    wecom_bot_enabled: bool = False
    wecom_bot_id: str = ""
    wecom_bot_secret: str = ""
    wecom_bot_ws_url: str = "wss://openws.work.weixin.qq.com"
    wecom_bot_allowed_user_ids: str = ""
    wecom_bot_allow_groups: bool = False
    wecom_bot_heartbeat_seconds: float = Field(default=30.0, ge=10.0, le=120.0)
    wecom_bot_reply_interval_seconds: float = Field(default=0.3, ge=0.1, le=2.0)

    wechat_kf_enabled: bool = False
    wechat_kf_corp_id: str = ""
    wechat_kf_secret: str = ""
    wechat_kf_open_kfid: str = ""
    wechat_kf_open_kf_ids: str = ""
    wechat_kf_callback_token: str = ""
    wechat_kf_callback_aes_key: str = ""
    wechat_kf_api_base_url: str = "https://qyapi.weixin.qq.com"
    wechat_kf_allowed_external_user_ids: str = ""
    wechat_kf_request_timeout_seconds: float = Field(default=20.0, gt=0.0, le=120.0)

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def wecom_bot_allowed_users(self) -> set[str]:
        return {
            item.strip()
            for item in self.wecom_bot_allowed_user_ids.split(",")
            if item.strip()
        }

    @property
    def wechat_kf_open_ids(self) -> set[str]:
        values = [self.wechat_kf_open_kfid, *self.wechat_kf_open_kf_ids.split(",")]
        return {item.strip() for item in values if item.strip()}

    @property
    def wechat_kf_allowed_external_users(self) -> set[str]:
        return {
            item.strip()
            for item in self.wechat_kf_allowed_external_user_ids.split(",")
            if item.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()

