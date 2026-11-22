"""Settings for the ingest service, read from BTP_* environment variables."""

from typing import Literal

from pydantic import Field

from btp_common.config import BaseServiceSettings

VBB_RT_URL = "https://production.gtfsrt.vbb.de/data"
MIN_POLL_INTERVAL_S = 10.0


class IngestSettings(BaseServiceSettings):
    """Feed mode, source and polling settings."""

    service_name: str = "ingest"
    # live polls VBB; fixtures replays fixtures/vbb-rt/<fixture_set>/ without network.
    feed_mode: Literal["live", "fixtures"] = "live"
    feed_url: str = VBB_RT_URL
    # Sent in the User-Agent so VBB can reach us. Required in live mode; no default.
    feed_contact: str | None = None
    poll_interval_s: float = Field(default=30.0, ge=MIN_POLL_INTERVAL_S)
    # Older headers are logged as stale but still passed on.
    feed_max_age_s: float = Field(default=180.0, gt=0)
    fixture_set: str = "rush-hour"
    # Kafka: 127.0.0.1:9094 is the compose broker's host listener (IPv4 only);
    # compose sets kafka:9092.
    kafka_bootstrap: str = "127.0.0.1:9094"
    kafka_topic: str = "trip-updates"
    # A message not delivered within this fails; the next poll retries the snapshot.
    publish_timeout_s: float = Field(default=10.0, gt=0)
