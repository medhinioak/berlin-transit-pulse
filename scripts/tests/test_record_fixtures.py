"""Tests for the recorder's rate guard and request helpers."""

import httpx
import pytest
from record_fixtures import MIN_INTERVAL_S, Pacer, retry_after_s, user_agent


class FakeClock:
    """A clock that only moves when sleep is called."""

    def __init__(self) -> None:
        """Start at t = 1000 s."""
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        """Return the current fake time."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance the fake time."""
        self.sleeps.append(seconds)
        self.now += seconds


def test_requests_are_never_closer_than_the_interval() -> None:
    """Back-to-back calls are spaced by the interval: at most 6 per minute at 10 s."""
    clock = FakeClock()
    pacer = Pacer(10, clock=clock.time, sleep=clock.sleep)
    starts = []
    for _ in range(7):
        pacer.wait()
        starts.append(clock.now)
    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    assert min(gaps) >= 10
    assert sum(1 for t in starts if t < starts[0] + 60) <= 6


def test_first_request_does_not_wait() -> None:
    """The first request goes out immediately."""
    clock = FakeClock()
    Pacer(30, clock=clock.time, sleep=clock.sleep).wait()
    assert clock.sleeps == []


def test_retry_after_extends_but_never_shortens_the_wait() -> None:
    """Retry-After above the interval wins; a shorter one still waits the full interval."""
    clock = FakeClock()
    pacer = Pacer(10, clock=clock.time, sleep=clock.sleep)
    pacer.wait()
    pacer.wait(at_least_s=45)
    pacer.wait(at_least_s=2)
    assert clock.sleeps == [45, 10]


def test_slow_caller_is_not_delayed_further() -> None:
    """If the interval has already passed, wait returns at once."""
    clock = FakeClock()
    pacer = Pacer(10, clock=clock.time, sleep=clock.sleep)
    pacer.wait()
    clock.now += 25
    pacer.wait()
    assert clock.sleeps == []


@pytest.mark.parametrize("interval", [0, 5, MIN_INTERVAL_S - 0.1])
def test_intervals_below_the_minimum_are_refused(interval: float) -> None:
    """Nobody can configure more than 6 requests per minute."""
    with pytest.raises(ValueError):
        Pacer(interval)


@pytest.mark.parametrize(
    ("header", "expected"), [(None, 0.0), ("30", 30.0), ("soon", 0.0), ("-5", 0.0)]
)
def test_retry_after_parsing(header: str | None, expected: float) -> None:
    """Retry-After in seconds is read; missing, negative or HTTP-date values count as 0."""
    headers = {"retry-after": header} if header is not None else {}
    assert retry_after_s(httpx.Response(429, headers=headers)) == expected


def test_user_agent_carries_contact() -> None:
    """The User-Agent names the project and the contact, as VBB asks."""
    assert user_agent("https://example.org") == "berlin-transit-pulse/0.1 (+https://example.org)"
