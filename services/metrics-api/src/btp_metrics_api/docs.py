"""Self-hosted Swagger UI page: no CDN and no inline script, so it passes the site's CSP."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# init.js is in the repository; vendor/ holds Swagger UI, added by the image build
# (services/metrics-api/Dockerfile). Without vendor/, /docs loads but stays blank.
ASSETS_DIR = Path(__file__).parent / "docs_assets"

# Relative links: the page works at /docs and, behind nginx, at /api/docs.
DOCS_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Berlin Transit Pulse metrics API</title>
  <link rel="stylesheet" href="docs-assets/vendor/swagger-ui.css">
  <link rel="icon" href="docs-assets/vendor/favicon-32x32.png">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="docs-assets/vendor/swagger-ui-bundle.js"></script>
  <script src="docs-assets/init.js"></script>
</body>
</html>
"""

router = APIRouter(include_in_schema=False)


@router.get("/docs", response_class=HTMLResponse)
def docs() -> str:
    """Return the Swagger UI page."""
    return DOCS_HTML


def assets() -> StaticFiles:
    """Serve init.js and the vendored Swagger UI files."""
    return StaticFiles(directory=ASSETS_DIR)
