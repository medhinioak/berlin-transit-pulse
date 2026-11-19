"""Settings for metrics-jobs, read from BTP_* environment variables."""

from pathlib import Path

from pydantic import Field

from btp_common.config import BaseServiceSettings

# Editable install (host, tests): src/btp_metrics_jobs/settings.py -> services/metrics-jobs/sql.
# The image has no source tree, so its Dockerfile copies sql/ and sets BTP_METRICS_SQL_DIR.
_SOURCE_SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


class MetricsJobsSettings(BaseServiceSettings):
    """Database settings and the SQL folder; the defaults match the compose dev stack."""

    service_name: str = "metrics-jobs"
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_db: str = "btp"
    pg_user: str = "btp_metrics_jobs"
    pg_metrics_jobs_password: str = "dev_metrics_jobs"
    metrics_sql_dir: Path = _SOURCE_SQL_DIR
    # The views read events.*, which the processor creates when it starts. In compose both
    # start together, so apply-sql waits for those tables this long before giving up.
    wait_for_tables_s: float = Field(default=60.0, ge=0)
