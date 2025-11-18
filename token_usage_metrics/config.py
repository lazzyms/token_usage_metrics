"""Configuration management for token usage metrics."""

from enum import Enum
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackendType(str, Enum):
    """Supported backend types."""

    REDIS = "redis"
    POSTGRES = "postgres"
    MONGODB = "mongodb"
    SUPABASE = "supabase"


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_prefix="TUM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Backend selection
    backend: BackendType = Field(default=BackendType.REDIS)

    # Redis settings
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_pool_size: int = Field(default=10, ge=1)
    redis_socket_timeout: float = Field(default=5.0, gt=0)
    redis_socket_connect_timeout: float = Field(default=5.0, gt=0)

    # Postgres settings
    postgres_dsn: str = Field(default="postgresql://localhost:5432/token_usage")
    postgres_pool_min_size: int = Field(default=2, ge=1)
    postgres_pool_max_size: int = Field(default=10, ge=1)
    postgres_command_timeout: float = Field(default=60.0, gt=0)

    # MongoDB settings
    mongodb_url: str = Field(default="mongodb://localhost:27017")
    mongodb_database: str = Field(default="token_usage")
    mongodb_max_pool_size: int = Field(default=10, ge=1)
    mongodb_timeout: float = Field(default=5.0, gt=0)

    # Supabase (Postgres-compatible) settings
    supabase_dsn: str = Field(default="postgresql://localhost:5432/token_usage")
    supabase_pool_min_size: int = Field(default=2, ge=1)
    supabase_pool_max_size: int = Field(default=10, ge=1)
    supabase_command_timeout: float = Field(default=60.0, gt=0)
    supabase_schema: str = Field(default="public")

    # Queue and buffering
    buffer_size: int = Field(default=1000, ge=10)
    flush_interval: float = Field(default=1.0, gt=0)  # seconds
    flush_batch_size: int = Field(default=200, ge=1)
    drop_policy: Literal["newest", "oldest"] = Field(default="oldest")

    # Retry and resilience
    max_retries: int = Field(default=3, ge=0)
    retry_backoff_base: float = Field(default=0.5, gt=0)
    retry_backoff_max: float = Field(default=10.0, gt=0)
    circuit_breaker_threshold: int = Field(default=5, ge=1)
    circuit_breaker_timeout: float = Field(default=60.0, gt=0)

    # Retention (0 = lifetime)
    default_ttl_days: int = Field(default=0, ge=0)  # 0 means no TTL

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    structured_logging: bool = Field(default=True)
