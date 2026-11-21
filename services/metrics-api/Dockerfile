# metrics-api image (pattern: services/ingest/Dockerfile). Build from the repo root:
#   docker build -f services/metrics-api/Dockerfile .

ARG PACKAGE=btp-metrics-api

FROM python:3.12-slim AS build
ARG PACKAGE
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

# 1. Third-party dependencies only, from the lockfile and the pyproject files this package
#    needs. This layer is reused until uv.lock or those files change.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=libs/common/pyproject.toml,target=libs/common/pyproject.toml \
    --mount=type=bind,source=services/metrics-api/pyproject.toml,target=services/metrics-api/pyproject.toml \
    uv sync --frozen --no-dev --no-install-workspace --package "$PACKAGE"

# Swagger UI for the self-hosted /docs page (btp_metrics_api/docs.py), pinned and checked
# against the SHA-256 of the npm tarball. Bump both together:
#   curl -sL https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-<version>.tgz | shasum -a 256
ARG SWAGGER_UI_VERSION=5.34.1
ARG SWAGGER_UI_SHA256=8e7286de0230e431fc8a5a75490d28117af3d0c306413b6f9e17f7bb55045fb6
ADD --checksum=sha256:${SWAGGER_UI_SHA256} \
    https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-${SWAGGER_UI_VERSION}.tgz \
    /tmp/swagger-ui.tgz

# 2. The workspace code (filtered by .dockerignore), plus the Swagger UI files inside the
#    package, so they are installed with it. --no-editable installs the packages into the
#    venv, so the runtime image needs no source.
COPY . .
RUN mkdir -p services/metrics-api/src/btp_metrics_api/docs_assets/vendor \
    && tar -xzf /tmp/swagger-ui.tgz --strip-components=1 \
       -C services/metrics-api/src/btp_metrics_api/docs_assets/vendor \
       package/swagger-ui-bundle.js package/swagger-ui.css package/favicon-32x32.png \
       package/LICENSE
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package "$PACKAGE"

FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home btp
# Same path as in the build stage: the venv's scripts have it in their shebang.
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1
USER btp
EXPOSE 8000
# CI runs the image with its default command and expects it to exit 0, so the default only
# checks that the service and its dependencies import. Compose runs `btp-metrics-api`.
CMD ["python", "-c", "import btp_metrics_api.server"]
