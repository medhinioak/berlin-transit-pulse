"""Settings for metrics-api, read from BTP_* environment variables."""

import gzip
from datetime import UTC, datetime
from typing import Literal

from google.transit.gtfs_realtime_pb2 import FeedMessage
from pydantic import Field, field_validator

from btp_common.config import BaseServiceSettings
from btp_common.fixtures import rt_fixture_paths


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
    # Reference time for the "now" views (ADR-0018). Unset in live mode: the views use now().
    # In fixtures mode it defaults to the header time of the replayed set's last snapshot.
    as_of: datetime | None = None
    feed_mode: Literal["live", "fixtures"] = "live"
    fixture_set: str = "rush-hour"

    @field_validator("as_of", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        """Treat BTP_AS_OF= (compose passes it through empty) as not set."""
        return None if isinstance(value, str) and not value.strip() else value


def fixture_as_of(label: str) -> datetime:
    """Return the header time of the last snapshot of a fixture set: when it was recorded."""
    feed = FeedMessage()
    feed.ParseFromString(gzip.decompress(rt_fixture_paths(label)[-1].read_bytes()))
    return datetime.fromtimestamp(feed.header.timestamp, tz=UTC)


def reference_time(settings: ApiSettings) -> datetime | None:
    """Return the fixed reference time for the views, or None to use the database's now()."""
    if settings.as_of is not None:
        return settings.as_of
    if settings.feed_mode == "fixtures":
        return fixture_as_of(settings.fixture_set)
    return None
