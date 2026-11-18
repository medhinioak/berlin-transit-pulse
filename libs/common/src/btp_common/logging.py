"""JSON logging to stdout, one object per line."""

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON with a UTC timestamp and the service name."""

    def __init__(self, service_name: str) -> None:
        """Remember the service name to add to every record."""
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        """Return the record as a JSON string."""
        entry: dict[str, str] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(service_name: str, level: str = "INFO") -> None:
    """Send all logging through one stdout handler that writes JSON lines."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
