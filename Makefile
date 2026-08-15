# resumaker - dev tasks. Everything runs through `uv` (never pip).
.DEFAULT_GOAL := help
.PHONY: help install lint fmt type test test-live verify doctor api cli docker-build docker-up docker-down

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Sync all deps (core + api + scrape + dev)
	uv sync --all-extras

lint:  ## Ruff lint
	uv run ruff check src apps tests

fmt:  ## Ruff format
	uv run ruff format src apps tests

type:  ## Mypy type-check the library
	uv run mypy src

test:  ## Unit + integration tests (skips live)
	uv run pytest

test-live:  ## Include tests that hit real providers/network
	uv run pytest -m live

verify:  ## Governance conformance: recipes/cards well-formed + paired
	uv run python scripts/verify.py

doctor:  ## Read-only health report + PII scan over the git index
	uv run python scripts/doctor.py

api:  ## Run the API locally (reload)
	uv run uvicorn apps.api.main:app --reload --port 8000

cli:  ## CLI passthrough, e.g. `make cli ARGS="run <url>"`
	uv run python -m apps.cli $(ARGS)

docker-build:  ## Build the deploy image
	docker build -f deploy/Dockerfile -t resumaker:local .

docker-up:  ## Start api + Caddy
	cd deploy && docker compose up -d --build

docker-down:  ## Stop the stack
	cd deploy && docker compose down
