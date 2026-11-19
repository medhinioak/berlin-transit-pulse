"""Settings for the stop-event processor, read from BTP_* environment variables."""

from pydantic import Field

from btp_common.config import BaseServiceSettings


class ProcessorSettings(BaseServiceSettings):
    """Kafka, database and batching settings; the defaults match the compose dev stack."""

    service_name: str = "processor"
    # localhost:9094 is the compose broker's host listener; compose sets kafka:9092.
    kafka_bootstrap: str = "localhost:9094"
    kafka_topic: str = "trip-updates"
    kafka_group: str = "btp-processor"
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_db: str = "btp"
    pg_user: str = "btp_processor"
    pg_processor_password: str = "dev_processor"
    # A batch is written in one transaction and its offsets committed after it.
    batch_max_messages: int = Field(default=500, ge=1)
    batch_max_wait_s: float = Field(default=1.0, gt=0)
