# Vigil — common commands. Run `make help` for the list.
SHELL := /bin/bash
.DEFAULT_GOAL := help

BACKEND  := backend
FRONTEND := frontend
DAYS ?=

.PHONY: help install dev up down logs check keygen migrate seed test lint tunnel

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-9s %s\n", $$1, $$2}'

install: ## Install backend (uv) and frontend (pnpm) dependencies
	cd $(BACKEND) && uv sync
	cd $(FRONTEND) && pnpm install --frozen-lockfile

$(FRONTEND)/node_modules/.modules.yaml: $(FRONTEND)/package.json $(FRONTEND)/pnpm-lock.yaml
	cd $(FRONTEND) && pnpm install --frozen-lockfile

dev: $(FRONTEND)/node_modules/.modules.yaml ## Local dev: Caddy :8080 → backend :8000 (reload) + frontend :3000 (HMR)
	@command -v caddy >/dev/null || { echo "caddy not found — brew install caddy (or use 'make up' for Docker)"; exit 1; }
	@echo "Vigil dev → http://localhost:8080   (Ctrl-C stops everything)"
	@trap 'kill 0' INT TERM; \
	  (cd $(BACKEND) && uv run uvicorn app.main:app --reload --reload-dir app --host 127.0.0.1 --port 8000) & \
	  (cd $(FRONTEND) && pnpm dev --hostname 127.0.0.1 --port 3000) & \
	  caddy run --config Caddyfile --adapter caddyfile & \
	  wait

up: ## Docker Compose: build and start Caddy + backend + frontend on :8080
	docker compose up --build -d
	@echo "Vigil → http://localhost:8080"

down: ## Docker Compose: stop the stack
	docker compose down

logs: ## Docker Compose: follow logs
	docker compose logs -f

check: ## Verify Tiger + Snowflake connections (PASS/FAIL table)
	cd $(BACKEND) && uv run python scripts/check_connections.py

keygen: ## Generate the Snowflake RSA key pair in ./secrets and print the SQL to register it
	cd $(BACKEND) && uv run python scripts/gen_snowflake_keypair.py

migrate: ## Apply Tiger SQL migrations in order (backend/sql/tiger)
	cd $(BACKEND) && uv run python scripts/migrate.py

seed: ## Seed synthetic history: make seed DAYS=3 (default: SEED_DAYS from .env)
	cd $(BACKEND) && uv run python scripts/seed.py $(if $(DAYS),--days $(DAYS),)

test: ## Backend pytest + frontend vitest
	cd $(BACKEND) && uv run pytest -q
	cd $(FRONTEND) && pnpm test

lint: ## ruff + eslint + tsc --noEmit
	cd $(BACKEND) && uv run ruff check . && uv run ruff format --check .
	cd $(FRONTEND) && pnpm lint && pnpm typecheck

tunnel: ## Public HTTPS URL for the iPhone pages (cloudflared → localhost:8080)
	cloudflared tunnel --url http://localhost:8080
