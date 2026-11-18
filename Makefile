.DEFAULT_GOAL := help
.PHONY: help install test test-integration lint fmt typecheck up down logs fixtures tf-plan

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

# pytest exits 5 when no test is collected; until #3 adds integration tests that is not a failure.
test-integration: ## Integration tests (needs `make up`)
	uv run pytest -m integration; status=$$?; [ $$status -eq 0 ] || [ $$status -eq 5 ]

lint: ## Ruff lint and format check
	uv run ruff check . && uv run ruff format --check .

fmt: ## Format and auto-fix with ruff
	uv run ruff check --fix . && uv run ruff format .

typecheck: ## mypy (strict) on every package
	@set -e; for p in $(PACKAGES); do echo "mypy $$p"; uv run mypy $$p/src $$p/tests; done

up down logs: ## Start / stop / follow the local stack (not yet: see #3)
	@echo "make $@: not implemented yet, see #3"; exit 1

fixtures: ## Record new feed fixtures (not yet: see #4)
	@echo "make $@: not implemented yet, see #4"; exit 1

tf-plan: ## Terraform fmt, validate, plan (not yet: see #13)
	@echo "make $@: not implemented yet, see #13"; exit 1
