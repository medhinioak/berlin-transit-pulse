"""Tests for BaseServiceSettings."""

import pytest
from pydantic import ValidationError

from btp_common.config import BaseServiceSettings


def test_reads_btp_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BTP_SERVICE_NAME", "ingest")
    monkeypatch.setenv("BTP_LOG_LEVEL", "DEBUG")
    settings = BaseServiceSettings()
    assert settings.service_name == "ingest"
    assert settings.log_level == "DEBUG"


def test_log_level_defaults_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BTP_SERVICE_NAME", "ingest")
    monkeypatch.delenv("BTP_LOG_LEVEL", raising=False)
    assert BaseServiceSettings().log_level == "INFO"


def test_unprefixed_env_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BTP_SERVICE_NAME", raising=False)
    monkeypatch.setenv("SERVICE_NAME", "ingest")
    with pytest.raises(ValidationError):
        BaseServiceSettings()


def test_subclass_adds_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    class IngestSettings(BaseServiceSettings):
        feed_mode: str = "live"

    monkeypatch.setenv("BTP_SERVICE_NAME", "ingest")
    monkeypatch.setenv("BTP_FEED_MODE", "fixtures")
    assert IngestSettings().feed_mode == "fixtures"
