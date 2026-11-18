"""Tests for BaseServiceSettings."""

import pytest
from pydantic import ValidationError

from btp_common.config import BaseServiceSettings


def test_reads_btp_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """BTP_* variables fill the matching fields."""
    monkeypatch.setenv("BTP_SERVICE_NAME", "ingest")
    monkeypatch.setenv("BTP_LOG_LEVEL", "DEBUG")
    settings = BaseServiceSettings()
    assert settings.service_name == "ingest"
    assert settings.log_level == "DEBUG"


def test_log_level_defaults_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without BTP_LOG_LEVEL the level is INFO."""
    monkeypatch.setenv("BTP_SERVICE_NAME", "ingest")
    monkeypatch.delenv("BTP_LOG_LEVEL", raising=False)
    assert BaseServiceSettings().log_level == "INFO"


def test_log_level_is_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lower case and surrounding whitespace are accepted."""
    monkeypatch.setenv("BTP_SERVICE_NAME", "ingest")
    monkeypatch.setenv("BTP_LOG_LEVEL", " warning ")
    assert BaseServiceSettings().log_level == "WARNING"


def test_unknown_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A level logging doesn't know fails when settings load, not later."""
    monkeypatch.setenv("BTP_SERVICE_NAME", "ingest")
    monkeypatch.setenv("BTP_LOG_LEVEL", "verbose")
    with pytest.raises(ValidationError):
        BaseServiceSettings()


def test_unprefixed_env_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """SERVICE_NAME without the prefix doesn't count."""
    monkeypatch.delenv("BTP_SERVICE_NAME", raising=False)
    monkeypatch.setenv("SERVICE_NAME", "ingest")
    with pytest.raises(ValidationError):
        BaseServiceSettings()


def test_subclass_adds_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Services add their own BTP_* fields by subclassing."""

    class IngestSettings(BaseServiceSettings):
        """Example service settings."""

        feed_mode: str = "live"

    monkeypatch.setenv("BTP_SERVICE_NAME", "ingest")
    monkeypatch.setenv("BTP_FEED_MODE", "fixtures")
    assert IngestSettings().feed_mode == "fixtures"
