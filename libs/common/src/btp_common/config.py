"""Typed settings read from BTP_* environment variables."""

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class BaseServiceSettings(BaseSettings):
    """Settings every service shares; services subclass this to add their own fields."""

    model_config = SettingsConfigDict(env_prefix="BTP_")

    service_name: str
    log_level: LogLevel = "INFO"

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        """Accept any case and surrounding whitespace, e.g. ' warning '."""
        return value.strip().upper() if isinstance(value, str) else value
