"""JSON logging to stdout, one object per line."""

import json
import logging
import sys
from datetime import UTC, datetime

from btp_common.config import BaseServiceSettings


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON with a UTC timestamp and the service name.

    Structured fields passed as ``extra={"fields": {...}}`` are added to the object; they
    never replace the standard keys (ts, level, service, logger, message, exc_info).
    """

    def __init__(self, service_name: str) -> None:
        """Remember the service name to add to every record."""
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        """Return the record as a JSON string."""
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            entry.update({str(k): v for k, v in fields.items() if k not in entry})
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(settings: BaseServiceSettings) -> None:
    """Send all logging through one stdout handler that writes JSON lines.

    Service name and level come from the settings, i.e. BTP_SERVICE_NAME and BTP_LOG_LEVEL.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(settings.service_name))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)
