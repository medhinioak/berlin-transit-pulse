"""Tests for the poll loop and the btp-ingest entry point."""

import json
import logging
import signal
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from google.transit.gtfs_realtime_pb2 import FeedMessage

from btp_common.logging import JsonFormatter
from btp_ingest import runner
from btp_ingest.feed import FeedUnavailable, InvalidSnapshot, Snapshot
from btp_ingest.publish import ProducerFatalError
from btp_ingest.runner import StopSignal, main, run
from btp_ingest.settings import IngestSettings

START = datetime(2026, 11, 18, 17, 3, 41, tzinfo=UTC)


class FakeLoop:
    """Fake clock and stop signal: wait() skips ahead and stops after max_polls waits."""

    def __init__(self, max_polls: int) -> None:
        """Stop once max_polls polls have finished."""
        self.t = 0.0
        self.max_polls = max_polls
        self.waits: list[float] = []

    def now(self) -> float:
        """Return monotonic seconds."""
        return self.t

    def is_set(self) -> bool:
        """Stop after max_polls."""
        return len(self.waits) >= self.max_polls

    def wait(self, timeout: float | None = None) -> bool:
        """Record the wait and skip ahead."""
        self.waits.append(timeout or 0.0)
        self.t += timeout or 0.0
        return self.is_set()


Result = Snapshot | None | Exception


class ScriptedSource:
    """Returns (or raises) scripted results; each fetch takes 0.5 s of fake time."""

    def __init__(self, loop: FakeLoop, *results: Result) -> None:
        """Answer with results in order, repeating the last one."""
        self.loop = loop
        self.results = list(results)

    def fetch(self) -> Snapshot | None:
        """Return the next scripted result."""
        self.loop.t += 0.5
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result

    def close(self) -> None:
        """Nothing to close."""


def snapshot(entities: int = 3) -> Snapshot:
    """A snapshot with a header 12 s old."""
    message = FeedMessage()
    for i in range(entities):
        message.entity.add(id=str(i))
    return Snapshot(
        feed_timestamp=START - timedelta(seconds=12),
        fetched_at=START,
        etag='"e"',
        raw_bytes_len=1000,
        wire_bytes=200,
        message=message,
    )


def run_scripted(*results: Result, polls: int) -> tuple[FakeLoop, list[Snapshot]]:
    """Run the loop for a number of polls and return the fake loop and handled snapshots."""
    loop = FakeLoop(polls)
    handled: list[Snapshot] = []
    run(ScriptedSource(loop, *results), handled.append, 30.0, loop, clock=loop.now)
    return loop, handled


def poll_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    """The poll log records, formatted as the service writes them."""
    formatter = JsonFormatter("ingest")
    return [json.loads(formatter.format(r)) for r in caplog.records if r.getMessage() == "poll"]


def test_polls_start_to_start_at_the_interval() -> None:
    """The fetch time counts towards the interval; the handler sees each snapshot."""
    snap = snapshot()
    loop, handled = run_scripted(snap, polls=3)
    assert loop.waits == [29.5, 29.5, 29.5]
    assert handled == [snap, snap, snap]


def test_304_is_not_handled() -> None:
    """No new data, no handler call, normal interval."""
    loop, handled = run_scripted(None, polls=2)
    assert handled == []
    assert loop.waits == [29.5, 29.5]


def test_retry_after_60_waits_60_s() -> None:
    """A 429 with Retry-After: 60 delays the next poll by 60 s."""
    loop, _ = run_scripted(FeedUnavailable("429", 60.0), None, polls=2)
    assert loop.waits == [59.5, 29.5]


def test_repeated_5xx_backs_off_to_the_cap_and_success_resets() -> None:
    """Gaps of 30, 60, 120, 240, 300, 300, then the interval again after a success."""
    errors = [FeedUnavailable("503")] * 6
    loop, handled = run_scripted(*errors, snapshot(), FeedUnavailable("network"), polls=8)
    gaps = [w + 0.5 for w in loop.waits]
    assert gaps == [30, 60, 120, 240, 300, 300, 30, 30]
    assert len(handled) == 1


def test_invalid_snapshot_is_skipped_and_counted(caplog: pytest.LogCaptureFixture) -> None:
    """Invalid snapshots don't reach the handler, are counted and don't back off."""
    caplog.set_level(logging.INFO)
    bad = InvalidSnapshot("not a FeedMessage")
    loop, handled = run_scripted(bad, bad, snapshot(), polls=3)
    assert len(handled) == 1
    assert loop.waits == [29.5, 29.5, 29.5]
    lines = poll_lines(caplog)
    assert [line["status"] for line in lines] == ["invalid", "invalid", "200"]
    assert [line["invalid_total"] for line in lines] == [1, 2, 2]
    assert lines[0]["reason"] == "not a FeedMessage"


def test_one_json_line_per_poll(caplog: pytest.LogCaptureFixture) -> None:
    """Each poll logs status, duration, bytes on the wire, entity count and header age."""
    caplog.set_level(logging.INFO)
    run_scripted(snapshot(entities=5), None, FeedUnavailable("503"), polls=3)
    ok, not_modified, error = poll_lines(caplog)
    assert ok["status"] == "200"
    assert ok["duration_ms"] == 500
    assert ok["wire_bytes"] == 200
    assert ok["entities"] == 5
    assert ok["header_age_s"] == 12.0
    assert not_modified["status"] == "304"
    assert not_modified["wire_bytes"] == 0
    assert error["status"] == "503"
    assert error["level"] == "WARNING"
    assert error["next_poll_s"] == 30.0


def test_network_error_detail_is_in_the_poll_line(caplog: pytest.LogCaptureFixture) -> None:
    """A network failure gives one poll line with the error, not a second log line."""
    caplog.set_level(logging.INFO)
    run_scripted(FeedUnavailable("network", detail="ConnectTimeout: timed out"), polls=1)
    [line] = poll_lines(caplog)
    assert line["status"] == "network"
    assert line["reason"] == "ConnectTimeout: timed out"
    assert len(caplog.records) == 1


def test_stop_ends_the_loop_during_the_wait() -> None:
    """A stop requested while waiting ends run() without another fetch."""
    loop = FakeLoop(max_polls=1)
    source = ScriptedSource(loop, None)
    run(source, lambda s: None, 30.0, loop, clock=loop.now)
    assert len(loop.waits) == 1


# --- main() -----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def restore_process_state() -> Iterator[None]:
    """Undo the signal handlers and logging that main() installs."""
    handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    root = logging.getLogger()
    root_handlers, level = root.handlers[:], root.level
    yield
    for sig, handler in handlers.items():
        signal.signal(sig, handler)
    root.handlers[:] = root_handlers
    root.setLevel(level)


def test_main_refuses_live_mode_without_contact(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live mode without BTP_FEED_CONTACT exits with 2 before any request."""
    monkeypatch.setenv("BTP_FEED_MODE", "live")
    monkeypatch.delenv("BTP_FEED_CONTACT", raising=False)
    assert main() == 2


def test_main_rejects_an_interval_below_10_s(monkeypatch: pytest.MonkeyPatch) -> None:
    """BTP_POLL_INTERVAL_S below 10 is refused."""
    monkeypatch.setenv("BTP_POLL_INTERVAL_S", "5")
    assert main() == 2


def test_interval_defaults_to_30_s(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default poll interval is 30 s, in live mode."""
    monkeypatch.delenv("BTP_POLL_INTERVAL_S", raising=False)
    monkeypatch.delenv("BTP_FEED_MODE", raising=False)
    settings = IngestSettings()
    assert settings.poll_interval_s == 30.0
    assert settings.feed_mode == "live"


def test_main_runs_fixtures_and_stops_on_sigterm(monkeypatch: pytest.MonkeyPatch) -> None:
    """In fixtures mode main() builds the replayer, and SIGTERM sets the stop signal."""
    monkeypatch.setenv("BTP_FEED_MODE", "fixtures")
    monkeypatch.setenv("BTP_FIXTURE_SET", "evening")
    calls = []

    def fake_run(
        source: runner.Source, handler: runner.Handler, interval_s: float, stop: StopSignal
    ) -> None:
        """Fetch once, then deliver SIGTERM as a container stop would."""
        snap = source.fetch()
        assert snap is not None
        handler(snap)
        signal.raise_signal(signal.SIGTERM)
        calls.append((interval_s, stop.is_set()))

    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(runner, "make_producer", lambda *_: QuietProducer())
    assert main() == 0
    assert calls == [(30.0, True)]


class QuietProducer:
    """A producer that accepts everything and delivers instantly."""

    def produce(self, *args: object, **kwargs: object) -> None:
        """Drop the message."""

    def poll(self, timeout: float = -1) -> int:
        """Nothing to serve."""
        return 0

    def flush(self, timeout: float = -1) -> int:
        """Nothing queued."""
        return 0


def test_main_live_waits_one_interval_before_the_first_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a (re)start in live mode, a stop during the first wait exits with no request."""
    monkeypatch.setenv("BTP_FEED_MODE", "live")
    monkeypatch.setenv("BTP_FEED_CONTACT", "https://example.org/contact")
    waits: list[float | None] = []

    def fake_wait(_event: object, timeout: float | None = None) -> bool:
        """Record the wait and report that a stop was requested meanwhile."""
        waits.append(timeout)
        return True

    def fail_run(*_args: object) -> None:
        """run() must not be reached."""
        raise AssertionError("polled before the startup wait ended")

    monkeypatch.setattr(threading.Event, "wait", fake_wait)
    monkeypatch.setattr(runner, "run", fail_run)
    monkeypatch.setattr(runner, "make_producer", lambda *_: QuietProducer())
    assert main() == 0
    assert waits == [30.0]


def test_main_exits_1_on_a_fatal_producer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fatal producer error ends the service with 1, so the container restarts."""
    monkeypatch.setenv("BTP_FEED_MODE", "fixtures")

    def fatal_run(*_args: object) -> None:
        """Fail as Publisher.handle does after a fatal error."""
        raise ProducerFatalError("fenced")

    monkeypatch.setattr(runner, "run", fatal_run)
    monkeypatch.setattr(runner, "make_producer", lambda *_: QuietProducer())
    assert main() == 1
