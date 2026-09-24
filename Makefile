SHELL := /bin/bash
COMPOSE := docker compose
OFFLINE := docker compose -f docker-compose.yml -f docker-compose.offline.yml
BACKEND := cd backend &&
env_value = $(shell grep -E '^$(1)=' .env 2>/dev/null | cut -d= -f2)
LLM_MODEL ?= $(call env_value,LLM_MODEL)
API_PORT ?= $(or $(call env_value,API_PORT),8000)

.PHONY: help setup up down logs migrate demo-user sample-audio demo models offline-up offline-down \
        test test-live lint format typecheck check mobile-install mobile-check \
        terraform-validate clean

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-20s %s\n", $$1, $$2}'

setup: ## Create .env, install backend (uv) and mobile (npm) dependencies
	@test -f .env || cp .env.example .env
	$(BACKEND) uv sync
	cd mobile && npm ci

up: ## Start the stack with AI providers from .env (cloud-like mode)
	$(COMPOSE) up -d --build --wait

down: ## Stop the stack (either mode)
	$(OFFLINE) --profile models down

logs: ## Follow API, worker and scheduler logs
	$(COMPOSE) logs -f api worker beat

migrate: ## Apply database migrations
	$(COMPOSE) run --rm migrate

demo-user: ## Create the demo user and print its API token
	@$(COMPOSE) exec -T api python manage.py ensure_demo_user

models: ## Download the Whisper and Ollama models (needs internet, run once)
	$(COMPOSE) build
	$(COMPOSE) run --rm --no-deps worker python manage.py download_whisper_model
	LLM_MODEL=$(LLM_MODEL) $(OFFLINE) --profile models run --rm ollama-pull

offline-up: ## Start the offline stack: local Whisper + Ollama, worker without internet
	$(OFFLINE) up -d --build --wait

offline-down: ## Stop the offline stack
	$(OFFLINE) down

sample-audio: ## Generate the synthetic demo recording if it is not present
	@test -f scripts/fixtures/sales_call.m4a || bash scripts/generate_sample_audio.sh scripts/fixtures/sales_call.m4a

demo: sample-audio ## Upload the sample audio, run an analysis, print the result
	VA_API=http://localhost:$(API_PORT) VA_TOKEN=$($(COMPOSE) exec -T api python manage.py ensure_demo_user) python3 scripts/demo.py

test: ## Backend test suite (needs Postgres: docker compose up -d postgres)
	$(COMPOSE) up -d --wait postgres
	$(BACKEND) uv run pytest

test-live: sample-audio ## Tests against real local models (host Ollama + faster-whisper)
	$(BACKEND) LIVE_AI=1 OLLAMA_BASE_URL=http://localhost:11434 uv run pytest tests/test_live_ai.py

lint: ## Lint backend and mobile
	$(BACKEND) uv run ruff check . && uv run ruff format --check .
	cd mobile && npm run lint

format: ## Format backend code
	$(BACKEND) uv run ruff format . && uv run ruff check --fix .

typecheck: ## mypy + tsc
	$(BACKEND) DJANGO_DEBUG=true uv run mypy .
	cd mobile && npm run typecheck

check: lint typecheck test ## Everything CI runs for code

mobile-install: ## Install mobile dependencies
	cd mobile && npm ci

mobile-check: ## Mobile lint, typecheck and tests
	cd mobile && npm run lint && npm run typecheck && npm test

terraform-validate: ## terraform fmt -check + validate (no AWS credentials needed)
	cd infrastructure/terraform && terraform fmt -check -recursive && terraform init -backend=false -input=false >/dev/null && terraform validate

clean: ## Stop everything and delete volumes (data AND downloaded models)
	$(OFFLINE) --profile models down -v
