.DEFAULT_GOAL := help
.PHONY: help install test test-integration lint fmt typecheck up down logs ps kafka-topics psql \
	fixtures tf-plan

COMPOSE := docker compose -f deploy/compose/compose.yaml

# Each package is type-checked on its own: every package has a tests/test_smoke.py,
# and mypy rejects two modules with the same name in one run.
# A package is a folder with a pyproject.toml, so stray files in services/ or libs/ don't count.
PACKAGES := $(patsubst %/,%,$(dir $(wildcard services/*/pyproject.toml libs/*/pyproject.toml)))

help: ## List targets
	@grep -E '^[a-zA-Z0-9_. -]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

install: ## Install all workspace packages and dev tools
	uv sync --all-packages

test: ## Unit tests with coverage
	uv run pytest --cov

test-integration: ## Integration tests (needs `make up`)
	uv run pytest -m integration

lint: ## Ruff lint and format check
	uv run ruff check . && uv run ruff format --check .

fmt: ## Format and auto-fix with ruff
	uv run ruff check --fix . && uv run ruff format .

typecheck: ## mypy (strict) on every package
	@set -e; for p in $(PACKAGES); do echo "mypy $$p"; uv run mypy $$p/src $$p/tests; done

# --wait returns while kafka-init is still running; `wait` blocks until the topics exist
# and fails if kafka-init failed.
up: ## Start Kafka, Postgres and the services; waits until healthy and topics exist
	$(COMPOSE) up -d --wait
	$(COMPOSE) wait kafka-init

down: ## Stop the stack (keeps data; add -v by hand to reset volumes)
	$(COMPOSE) down

logs: ## Follow the logs of all containers
	$(COMPOSE) logs -f

ps: ## Show containers and their health
	$(COMPOSE) ps -a

kafka-topics: ## Describe all Kafka topics
	$(COMPOSE) exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 --describe

psql: ## Open psql as the Postgres superuser
	$(COMPOSE) exec postgres psql -U postgres -d btp

fixtures: ## Record new feed fixtures (not yet: see #4)
	@echo "make $@: not implemented yet, see #4"; exit 1

tf-plan: ## Terraform fmt, validate, plan (not yet: see #13)
	@echo "make $@: not implemented yet, see #13"; exit 1
