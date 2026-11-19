"""Console script btp-metrics-api: JSON logging, then uvicorn."""

import sys

import uvicorn
from pydantic import ValidationError

from btp_common.logging import configure_logging
from btp_metrics_api.app import create_app
from btp_metrics_api.settings import ApiSettings


def main() -> int:
    """Serve the API until SIGTERM or SIGINT; return 2 on invalid settings."""
    try:
        settings = ApiSettings()
    except ValidationError as exc:
        print(f"btp-metrics-api: invalid settings:\n{exc}", file=sys.stderr)
        return 2
    configure_logging(settings)
    # log_config=None keeps uvicorn's loggers on the root JSON handler.
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
