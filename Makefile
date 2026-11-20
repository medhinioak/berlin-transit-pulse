.DEFAULT_GOAL := help
.PHONY: help install test test-integration lint fmt typecheck up down logs ps kafka-topics psql \
	fixtures tf-check-stack tf-validate tf-plan

COMPOSE := docker compose -f deploy/compose/compose.yaml
# Containers that run once and exit 0 (make up checks their exit codes).
ONESHOTS := kafka-init schedule-import metrics-jobs-apply

# Each package is type-checked on its own: every package has a tests/test_smoke.py,
# and mypy rejects two modules with the same name in one run.
# A package is a folder with a pyproject.toml, so stray files in services/ or libs/ don't count.
PACKAGES := $(patsubst %/,%,$(dir $(wildcard services/*/pyproject.toml libs/*/pyproject.toml)))

help: ## List targets
	@grep -E '^[a-zA-Z0-9_. -]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

install: ## Install all workspace packages and dev tools
	uv sync --all-packages

test: ## Unit tests with coverage (terminal report and coverage.xml)
	uv run pytest --cov --cov-report=term-missing --cov-report=xml

test-integration: ## Integration tests (needs `make up`)
	uv run pytest -m integration

lint: ## Ruff lint and format check
	uv run ruff check . && uv run ruff format --check .

fmt: ## Format and auto-fix with ruff
	uv run ruff check --fix . && uv run ruff format .

typecheck: ## mypy (strict) on every package
	@set -e; for p in $(PACKAGES); do echo "mypy $$p"; uv run mypy $$p/src $$p/tests; done
	@echo "mypy scripts"; uv run mypy scripts

# --wait returns while the one-shot containers are still running; `wait` blocks until they
# finish and fails if one failed (topics created, schedule imported and active).
up: ## Start Kafka, Postgres and the services; waits until healthy, topics and schedule exist
	$(COMPOSE) up -d --build
	@# `up --wait` fails when a one-shot exits, even with 0, so check their exit codes here
	@# (`docker wait` works on running and stopped containers) and wait for the rest after.
	@for s in $(ONESHOTS); do \
	  code=$$(docker wait $$($(COMPOSE) ps -aq $$s)); \
	  [ "$$code" = 0 ] || { echo "$$s exited with $$code"; exit 1; }; \
	done
	$(COMPOSE) up -d --wait --no-deps $$($(COMPOSE) config --services | grep -vxF $(ONESHOTS:%=-e %))

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

# Calls the live feed: at most one request per 10 s (default 30 s). Needs BTP_FEED_CONTACT.
fixtures: ## Record a fixture set (LABEL=rush-hour|evening, COUNT=4), trim it, rebuild mini GTFS
	@test -n "$(LABEL)" || { echo "usage: make fixtures LABEL=rush-hour|evening [COUNT=4]"; exit 1; }
	uv run python scripts/record_fixtures.py --label $(LABEL) --count $(or $(COUNT),4)
	uv run python scripts/trim_fixtures.py fixtures/local/vbb-rt/$(LABEL) --out fixtures/vbb-rt/$(LABEL)
	uv run python scripts/build_static_fixture.py

# Terraform. Claude Code runs tf-validate only; a maintainer runs tf-plan and apply
# (deploy/terraform/README.md). TF_STATE_BUCKET is needed on a stack's first init.
# AWS_PROFILE defaults to btp locally; CI's OIDC env credentials take precedence over it.
TF = AWS_PROFILE=$(or $(AWS_PROFILE),btp) terraform -chdir=deploy/terraform/$(STACK)

tf-check-stack:
	@test -n "$(STACK)" -a -d "deploy/terraform/$(STACK)" || \
		{ echo "usage: make $(MAKECMDGOALS) STACK=<folder in deploy/terraform/>"; exit 1; }

tf-validate: tf-check-stack ## Terraform fmt check, init without backend, validate; no credentials (STACK=name)
	$(TF) fmt -check -recursive
	$(TF) init -backend=false -input=false
	$(TF) validate

tf-plan: tf-check-stack ## Terraform fmt check, init, validate, plan to plan.tfplan (STACK=name)
	$(TF) fmt -check -recursive
	$(TF) init -input=false $(if $(TF_STATE_BUCKET),-backend-config="bucket=$(TF_STATE_BUCKET)")
	$(TF) validate
	$(TF) plan -input=false -out=plan.tfplan
