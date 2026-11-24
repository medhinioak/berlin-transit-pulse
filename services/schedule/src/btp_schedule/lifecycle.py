"""Rules for staging, activating and pruning versions, without the database (ADR-0023)."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
# A feed may start this many days after today and still be imported, as a staged version.
STAGING_DAYS = 21
# Failed versions hold no rows; their versions row is kept this long so `status` shows why.
KEEP_FAILED = timedelta(days=7)


def berlin_today(now: datetime) -> date:
    """Return the Europe/Berlin calendar date of an aware datetime."""
    return now.astimezone(BERLIN).date()


def start_of_day(d: date) -> datetime:
    """Return 00:00 Europe/Berlin on d, as an aware datetime."""
    return datetime.combine(d, time(), tzinfo=BERLIN)


def date_range_errors(start: date, end: date, today: date) -> list[str]:
    """Return why a feed valid from start to end can't be imported today; empty if it can."""
    if end < today:
        return [f"feed dates {start}..{end} don't cover {today}"]
    if start > today + timedelta(days=STAGING_DAYS):
        return [
            f"feed starts on {start}, more than {STAGING_DAYS} days after {today}; "
            "it can be staged from "
            f"{start - timedelta(days=STAGING_DAYS)}"
        ]
    return []


def activate_on_for(feed_start: date | None, today: date) -> date | None:
    """Return the day a newly imported version should start: its start if not in the past."""
    if feed_start is not None and feed_start >= today:
        return feed_start
    return None


def effective_from(
    activated_at: datetime, activate_on: date | None, first_activation: bool
) -> datetime:
    """Return when an activation starts to apply to service days.

    A staged version activated for the first time applies from 00:00 on its activate_on day
    when it is activated later than that (catch-up, or a manual import on the day). Otherwise,
    including rollbacks and re-activations, it applies from activated_at.
    """
    if first_activation and activate_on is not None:
        return min(activated_at, start_of_day(activate_on))
    return activated_at


@dataclass(frozen=True)
class Staged:
    """A ready version and the day it starts."""

    version_id: str
    activate_on: date


def due_version(staged: Iterable[Staged], today: date, lead_days: int = 1) -> str | None:
    """Return the staged version to activate now, the latest start winning; None if none is due.

    With lead_days=1 (activate-due, on the eve at 23:40) a version starting tomorrow is due,
    so it is active before its first service day starts; one whose start has passed is caught
    up. With lead_days=0 (the morning refresh) only versions starting today or earlier are.
    """
    due = [s for s in staged if s.activate_on <= today + timedelta(days=lead_days)]
    if not due:
        return None
    return max(due, key=lambda s: (s.activate_on, s.version_id)).version_id


@dataclass(frozen=True)
class VersionState:
    """What pruning needs to know about one version."""

    version_id: str
    status: str
    created_at: datetime
    last_deactivated_at: datetime | None = None


def prune_candidates(versions: Iterable[VersionState], now: datetime) -> list[str]:
    """Return the versions to delete.

    Kept: the active version, the most recently deactivated retired one (late updates around a
    swap), every ready one (staged or waiting for a manual activation), importing ones, and
    failed ones younger than KEEP_FAILED. Deleted: older retired and older failed versions.
    """
    versions = list(versions)
    retired = [v for v in versions if v.status == "retired"]
    previous = max(
        retired,
        key=lambda v: (v.last_deactivated_at or datetime.min.replace(tzinfo=BERLIN), v.created_at),
        default=None,
    )
    doomed = [v.version_id for v in retired if v is not previous]
    doomed += [
        v.version_id for v in versions if v.status == "failed" and now - v.created_at > KEEP_FAILED
    ]
    return sorted(doomed)
