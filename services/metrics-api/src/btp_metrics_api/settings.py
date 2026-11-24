"""Settings for metrics-api, read from BTP_* environment variables."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator

from btp_common.config import BaseServiceSettings
from btp_common.fixtures import load_rt_fixture


class ApiSettings(BaseServiceSettings):
    """Server, database and freshness settings; the defaults match the compose dev stack."""

    service_name: str = "metrics-api"
    host: str = "127.0.0.1"
    port: int = 8000
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_db: str = "btp"
    pg_user: str = "btp_api"
    pg_api_password: str = "dev_api"
    pool_max_size: int = Field(default=4, ge=1)
    # /health fails when the newest feed time the processor applied is older than this.
    feed_max_age_s: int = Field(default=300, gt=0)
    # Reference time for the "now" views (ADR-0019). Unset in live mode: the views use now().
    # In fixtures mode it defaults to the header time of the replayed set's last snapshot.
    # Must carry an offset (e.g. ...Z): a naive time would be read in the session time zone.
    as_of: AwareDatetime | None = None
    feed_mode: Literal["live", "fixtures"] = "live"
    fixture_set: str = "rush-hour"

    @field_validator("as_of", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        """Treat BTP_AS_OF= (compose passes it through empty) as not set."""
        return None if isinstance(value, str) and not value.strip() else value


def fixture_as_of(label: str) -> datetime:
    """Return the header time of the last snapshot of a fixture set: when it was recorded."""
    return datetime.fromtimestamp(load_rt_fixture(label)[-1].header.timestamp, tz=UTC)


def fixture_trip_ids(label: str) -> frozenset[str]:
    """Return the trip_ids of every trip update in a fixture set."""
    return frozenset(
        entity.trip_update.trip.trip_id
        for feed in load_rt_fixture(label)
        for entity in feed.entity
        if entity.HasField("trip_update")
    )


def reference_time(settings: ApiSettings) -> datetime | None:
    """Return the fixed reference time for the views, or None to use the database's now()."""
    if settings.as_of is not None:
        return settings.as_of
    if settings.feed_mode == "fixtures":
        return fixture_as_of(settings.fixture_set)
    return None
