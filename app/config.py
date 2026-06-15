"""集中读取环境变量（pydantic-settings）。"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Anthropic
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # 飞书
    feishu_webhook_url: str = Field(default="", alias="FEISHU_WEBHOOK_URL")
    feishu_webhook_secret: str = Field(default="", alias="FEISHU_WEBHOOK_SECRET")

    # DB
    database_url: str = Field(
        default="postgresql+asyncpg://push:push@localhost:5432/pushtool",
        alias="DATABASE_URL",
    )

    # 抓取源
    s2_api_key: str = Field(default="", alias="S2_API_KEY")
    pubmed_email: str = Field(default="", alias="PUBMED_EMAIL")

    # 其他
    tz_default: str = Field(default="Asia/Shanghai", alias="TZ_DEFAULT")
    embedding_model: str = Field(default="BAAI/bge-m3", alias="EMBEDDING_MODEL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # 嵌入维度（bge-m3 = 1024）。换模型时同步改并重建迁移。
    embedding_dim: int = 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
