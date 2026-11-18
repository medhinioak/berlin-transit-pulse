# Reference Dockerfile for every service. Build from the repo root:
#   docker build -f services/ingest/Dockerfile .
# Other services copy this file and change PACKAGE, the service's pyproject.toml path in the
# first RUN, and CMD.

ARG PACKAGE=btp-ingest

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
    --mount=type=bind,source=services/ingest/pyproject.toml,target=services/ingest/pyproject.toml \
    uv sync --frozen --no-dev --no-install-workspace --package "$PACKAGE"

# 2. The workspace code (filtered by .dockerignore). --no-editable installs the packages into
#    the venv, so the runtime image needs no source.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package "$PACKAGE"

FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home btp
# Same path as in the build stage: the venv's scripts have it in their shebang.
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1
USER btp
# Placeholder until the service has an entry point (#7).
CMD ["python", "-c", "import btp_ingest"]
