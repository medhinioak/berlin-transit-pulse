"""Typed settings read from BTP_* environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    """Settings every service shares; services subclass this to add their own fields."""

    model_config = SettingsConfigDict(env_prefix="BTP_")

    service_name: str
    log_level: str = "INFO"
