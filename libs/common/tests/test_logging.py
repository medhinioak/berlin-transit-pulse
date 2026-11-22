"""Tests for JSON logging."""

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest

from btp_common.config import BaseServiceSettings, LogLevel
from btp_common.logging import RESERVED_KEYS, configure_logging


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


def _lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
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
    assert datetime.fromisoformat(str(entry["ts"])).utcoffset() == timedelta(0)


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
    assert "ValueError: boom" in str(entry["exc_info"])


def test_reconfiguring_does_not_duplicate_lines(capsys: pytest.CaptureFixture[str]) -> None:
    """Calling configure_logging twice keeps one handler."""
    _configure()
    _configure()
    logging.getLogger().warning("once")
    assert len(_lines(capsys)) == 1


def test_structured_fields_are_added(capsys: pytest.CaptureFixture[str]) -> None:
    """extra={"fields": ...} adds keys to the JSON object but can't overwrite standard ones."""
    _configure()
    logging.getLogger().info("poll", extra={"fields": {"status": 304, "message": "spoofed"}})
    [entry] = _lines(capsys)
    assert entry["status"] == 304
    assert entry["message"] == "poll"


def test_exc_info_field_without_an_exception_is_dropped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A field named exc_info can't make a healthy line look like an error."""
    _configure()
    logging.getLogger().info("poll", extra={"fields": {"exc_info": "fake", "status": "200"}})
    [entry] = _lines(capsys)
    assert "exc_info" not in entry
    assert entry["status"] == "200"


def test_exc_info_field_with_an_exception_keeps_the_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With a real exception, exc_info is the formatted traceback, not the field."""
    _configure()
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger().exception("failed", extra={"fields": {"exc_info": "fake"}})
    [entry] = _lines(capsys)
    assert "ValueError: boom" in str(entry["exc_info"])
    assert entry["exc_info"] != "fake"


@pytest.mark.parametrize("key", sorted(RESERVED_KEYS))
def test_fields_never_set_a_reserved_key(key: str, capsys: pytest.CaptureFixture[str]) -> None:
    """No reserved key can be overridden by a field."""
    _configure()
    logging.getLogger("btp.test").warning("poll", extra={"fields": {key: "spoofed"}})
    [entry] = _lines(capsys)
    expected = {"level": "WARNING", "service": "ingest", "logger": "btp.test", "message": "poll"}
    if key == "exc_info":
        assert key not in entry
    elif key == "ts":
        datetime.fromisoformat(str(entry["ts"]))  # the real timestamp, not the field
    else:
        assert entry[key] == expected[key]


def test_exception_logged_outside_an_except_block_has_no_exc_info(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """log.exception() with no active exception doesn't write "NoneType: None"."""
    _configure()
    logging.getLogger().exception("no exception here")
    [entry] = _lines(capsys)
    assert "exc_info" not in entry
    assert entry["level"] == "ERROR"
