"""Tests for JSON logging."""

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest

from btp_common.config import BaseServiceSettings, LogLevel
from btp_common.logging import configure_logging


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    """Undo configure_logging after each test."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _configure(level: LogLevel = "INFO") -> None:
    """Configure logging for a service called ingest."""
    configure_logging(BaseServiceSettings(service_name="ingest", log_level=level))


def _lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, str]]:
    """Parse captured stdout as JSON lines."""
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_writes_one_json_object_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    """A log call becomes one JSON line with message, service, level and logger."""
    _configure("INFO")
    logging.getLogger("btp.test").info("hello %s", "world")
    [entry] = _lines(capsys)
    assert entry["message"] == "hello world"
    assert entry["service"] == "ingest"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "btp.test"


def test_timestamp_is_utc(capsys: pytest.CaptureFixture[str]) -> None:
    """Timestamps carry a zero UTC offset."""
    _configure()
    logging.getLogger().warning("x")
    [entry] = _lines(capsys)
    assert datetime.fromisoformat(entry["ts"]).utcoffset() == timedelta(0)


def test_service_and_level_come_from_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BTP_SERVICE_NAME and BTP_LOG_LEVEL set the service field and drop lower records."""
    monkeypatch.setenv("BTP_SERVICE_NAME", "processor")
    monkeypatch.setenv("BTP_LOG_LEVEL", " warning ")
    configure_logging(BaseServiceSettings())
    logging.getLogger().info("dropped")
    logging.getLogger().warning("kept")
    entries = _lines(capsys)
    assert [e["message"] for e in entries] == ["kept"]
    assert entries[0]["service"] == "processor"


def test_exception_is_serialised(capsys: pytest.CaptureFixture[str]) -> None:
    """The traceback goes into exc_info."""
    _configure()
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger().exception("failed")
    [entry] = _lines(capsys)
    assert "ValueError: boom" in entry["exc_info"]


def test_reconfiguring_does_not_duplicate_lines(capsys: pytest.CaptureFixture[str]) -> None:
    """Calling configure_logging twice keeps one handler."""
    _configure()
    _configure()
    logging.getLogger().warning("once")
    assert len(_lines(capsys)) == 1
