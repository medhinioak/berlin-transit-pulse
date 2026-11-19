"""Response models and the logic that shapes them; no database or HTTP code here."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ATTRIBUTION = "Data: VBB Verkehrsverbund Berlin-Brandenburg GmbH, CC-BY 4.0"


class ModeNow(BaseModel):
    """Trips of one mode running now in Berlin and their current delay (seconds)."""

    mode: str = Field(description="bus, tram, ubahn, sbahn, regional or ferry")
    running_trips: int = Field(description="Berlin trips between their first and last stop now")
    trips_with_nonzero_delay: int = Field(description="running trips whose current delay isn't 0")
    avg_delay_s: float | None = Field(description="mean current delay; null without delays")
    p50_delay_s: float | None = Field(description="median current delay")
    p95_delay_s: float | None = Field(description="95th percentile of the current delay")
    cancelled_trips_today: int = Field(description="cancelled Berlin trips of today's service day")
    latest_feed_ts: datetime | None = Field(description="newest feed time in the database")
    computed_at: datetime = Field(description="when the database computed the row")


class NowByModeResponse(BaseModel):
    """GET /v1/now/by-mode: a live indicator, not a reliability or punctuality metric."""

    kind: Literal["live-indicator"] = "live-indicator"
    as_of: datetime = Field(description="the moment the figures describe")
    attribution: str = ATTRIBUTION
    modes: list[ModeNow]


class Health(BaseModel):
    """GET /health: whether the database answers and the feed data is fresh."""

    status: Literal["ok", "unavailable"]
    reason: str | None = None
    latest_feed_ts: datetime | None = None
    feed_age_s: float | None = None


def shape_now_by_mode(
    rows: list[dict[str, Any]], as_of: datetime | None, now: datetime
) -> NowByModeResponse:
    """Build the response from the view rows; as_of is the pinned reference time, if any."""
    modes = [ModeNow.model_validate(row) for row in rows]
    if as_of is None:
        as_of = modes[0].computed_at if modes else now
    return NowByModeResponse(as_of=as_of, modes=modes)


def check_health(latest_feed_ts: datetime | None, now: datetime, max_age_s: int) -> Health:
    """Return ok if the newest feed time is younger than max_age_s, else the reason."""
    if latest_feed_ts is None:
        return Health(status="unavailable", reason="no feed data in the database")
    age = (now - latest_feed_ts).total_seconds()
    if age >= max_age_s:
        return Health(
            status="unavailable",
            reason=f"feed data is {age:.0f} s old (limit {max_age_s} s)",
            latest_feed_ts=latest_feed_ts,
            feed_age_s=age,
        )
    return Health(status="ok", latest_feed_ts=latest_feed_ts, feed_age_s=age)


def database_unavailable(error: Exception) -> Health:
    """Return the health response for a database that can't be reached or queried."""
    return Health(status="unavailable", reason=f"database unavailable: {type(error).__name__}")
