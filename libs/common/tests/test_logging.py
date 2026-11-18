"""Tests for JSON logging."""

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest

from btp_common.logging import configure_logging


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, str]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_writes_one_json_object_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("ingest", "INFO")
    logging.getLogger("btp.test").info("hello %s", "world")
    [entry] = _lines(capsys)
    assert entry["message"] == "hello world"
    assert entry["service"] == "ingest"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "btp.test"


def test_timestamp_is_utc(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("ingest")
    logging.getLogger().warning("x")
    [entry] = _lines(capsys)
    assert datetime.fromisoformat(entry["ts"]).utcoffset() == timedelta(0)


def test_level_filter_applies(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("ingest", "warning")
    logging.getLogger().info("dropped")
    logging.getLogger().warning("kept")
    assert [e["message"] for e in _lines(capsys)] == ["kept"]


def test_exception_is_serialised(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("ingest")
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger().exception("failed")
    [entry] = _lines(capsys)
    assert "ValueError: boom" in entry["exc_info"]


def test_reconfiguring_does_not_duplicate_lines(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("ingest")
    configure_logging("ingest")
    logging.getLogger().warning("once")
    assert len(_lines(capsys)) == 1
