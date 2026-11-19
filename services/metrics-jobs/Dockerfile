# metrics-jobs image (pattern: services/ingest/Dockerfile). Build from the repo root:
#   docker build -f services/metrics-jobs/Dockerfile .

ARG PACKAGE=btp-metrics-jobs

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
    --mount=type=bind,source=services/metrics-jobs/pyproject.toml,target=services/metrics-jobs/pyproject.toml \
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
# The view SQL lives next to the package, not in it (services/metrics-jobs/sql/views/).
COPY services/metrics-jobs/sql /app/sql
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    BTP_METRICS_SQL_DIR=/app/sql
USER btp
# Compose runs `btp-metrics-jobs apply-sql`; the default needs no database (CI runs it).
CMD ["btp-metrics-jobs", "--help"]
