"""Settings for the schedule service, read from BTP_* environment variables."""

from pathlib import Path
from typing import Literal

from btp_common.config import BaseServiceSettings

VBB_STATIC_URL = "https://www.vbb.de/vbbgtfs"
REPO_URL = "https://github.com/medhinioak/berlin-transit-pulse"


class ScheduleSettings(BaseServiceSettings):
    """Database, download and mode settings; the defaults match the compose dev stack."""

    service_name: str = "schedule"
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_db: str = "btp"
    pg_user: str = "btp_schedule"
    pg_schedule_password: str = "dev_schedule"
    schedule_url: str = VBB_STATIC_URL
    schedule_cache_dir: Path = Path.home() / ".cache" / "btp-schedule"
    # A cached zip younger than this is used without asking the server (it sends no ETag).
    schedule_max_age_hours: float = 24.0
    # Sent in the User-Agent so VBB can reach us; the repo URL by default.
    feed_contact: str = REPO_URL
    # fixtures: `import` without --url/--file imports the mini GTFS from fixtures/, offline.
    feed_mode: Literal["live", "fixtures"] = "live"
    # Data bucket for zip backups (backups/gtfs/, btp_schedule.backup); unset means off.
    raw_bucket: str | None = None
    # Passed to boto3 as region_name (botocore ignores AWS_REGION; only AWS_DEFAULT_REGION).
    aws_region: str = "eu-north-1"
