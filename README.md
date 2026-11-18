# berlin-transit-pulse

[![CI](https://github.com/medhinioak/berlin-transit-pulse/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/medhinioak/berlin-transit-pulse/actions/workflows/ci.yml)

Live reliability metrics for Berlin public transport, built from VBB’s open realtime feed.

## Develop locally

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), for example
   `brew install uv`. uv provides Python 3.12 (`uv python install 3.12`) if you don't have it.
2. `make install` installs every service and the dev tools into `.venv`.
3. `uv run pre-commit install` runs ruff and the file checks on every commit.
4. `make test` runs the unit tests. `make lint` and `make typecheck` run ruff and mypy.

On macOS, keep the clone outside iCloud-synced folders such as `~/Documents`
(see `docs/troubleshooting.md`). `make help` lists all targets. Services live in `services/`, shared code in `libs/common`.

## Data

Realtime and timetable data: [VBB Verkehrsverbund Berlin-Brandenburg GmbH](https://www.vbb.de/vbb-services/api-open-data/),
licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
