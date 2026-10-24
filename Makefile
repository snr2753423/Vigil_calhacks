# Vigil — common commands. Run `make help` for the list.
SHELL := /bin/bash
.DEFAULT_GOAL := help

BACKEND  := backend
FRONTEND := frontend
DAYS ?=

.PHONY: help install dev up down logs check keygen migrate migrate-sf seed sf-load train detect care-plans prepare reset test lint tunnel \
	deploy-setup deploy-secrets deploy deploy-status deploy-logs deploy-reset

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-9s %s\n", $$1, $$2}'

install: ## Install backend (uv) and frontend (pnpm) dependencies
	cd $(BACKEND) && uv sync
	cd $(FRONTEND) && pnpm install --frozen-lockfile

$(FRONTEND)/node_modules/.modules.yaml: $(FRONTEND)/package.json $(FRONTEND)/pnpm-lock.yaml
	cd $(FRONTEND) && pnpm install --frozen-lockfile

dev: $(FRONTEND)/node_modules/.modules.yaml ## Local dev: Caddy :8080 → backend :8000 (reload) + frontend :3000 (HMR)
	@command -v caddy >/dev/null || { echo "caddy not found — brew install caddy (or use 'make up' for Docker)"; exit 1; }
	@busy=$$(for p in 8000 3000 8080; do lsof -nP -iTCP:$$p -sTCP:LISTEN -t >/dev/null 2>&1 && printf ' %s' $$p; done); \
	  if [ -n "$$busy" ]; then echo "port(s)$$busy already in use — stop the old stack first (lsof -nP -iTCP:<port> -sTCP:LISTEN)"; exit 1; fi
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

migrate-sf: ## Create the Snowflake VIGIL.CORE tables (backend/sql/snowflake/002, idempotent)
	cd $(BACKEND) && uv run python scripts/migrate_snowflake.py

seed: ## Seed synthetic history: make seed DAYS=3 (default: SEED_DAYS from .env), then bulk-load Snowflake
	cd $(BACKEND) && uv run python scripts/seed.py $(if $(DAYS),--days $(DAYS),)

sf-load: ## Re-run the Snowflake bulk load (Tiger vitals_15m + events + residents/staff → VIGIL.CORE)
	cd $(BACKEND) && uv run python scripts/sync_snowflake.py

train: ## Train the 5 Snowflake ANOMALY_DETECTION models (last night held out), then detect since
	cd $(BACKEND) && uv run python -u scripts/train_models.py

detect: ## Run Snowflake anomaly detection over last night (or --from/--to) and summarize the flags
	cd $(BACKEND) && uv run python -u scripts/detect_anomalies.py

prepare: ## Prepare last night's replay: detection, gated scoring pass, Cortex cards (also /admin → Prepare replay)
	cd $(BACKEND) && uv run python -u scripts/prepare_replay.py

reset: ## Demo to a known-good state + READY checklist (ARGS="--retrain" to retrain models, ~67 min)
	cd $(BACKEND) && uv run python -u scripts/reset_demo.py $(ARGS)

care-plans: ## Generate care-plan PDFs, upload + parse + chunk in Snowflake, build Cortex Search (003_docs.sql)
	cd $(BACKEND) && uv run python -u scripts/index_care_plans.py

test: ## Backend pytest + frontend vitest
	cd $(BACKEND) && uv run pytest -q
	cd $(FRONTEND) && pnpm test

lint: ## ruff + eslint + tsc --noEmit
	cd $(BACKEND) && uv run ruff check . && uv run ruff format --check .
	cd $(FRONTEND) && pnpm lint && pnpm typecheck

tunnel: ## Public HTTPS URL for the iPhone pages (cloudflared → localhost:8080); put it in .env as PUBLIC_URL
	@command -v cloudflared >/dev/null || { echo "cloudflared not found — brew install cloudflared"; exit 1; }
	@echo "Copy the https://….trycloudflare.com URL below into .env as PUBLIC_URL=… (no restart needed),"
	@echo "then scan the QR code on http://localhost:8080/devices with the iPhone."
	cloudflared tunnel --url http://localhost:8080

# --- Vultr deploy (DEPLOY.md). DEPLOY_HOST and DOMAIN come from .env.
deploy-setup: ## Server, once: Docker, swap, firewall
	scripts/deploy.sh setup

deploy-secrets: ## Copy .env + secrets/ to the server over SSH
	scripts/deploy.sh secrets

deploy: ## Copy code to the server, rebuild, restart, wait for https://DOMAIN
	scripts/deploy.sh push

deploy-status: ## Server containers + health
	scripts/deploy.sh status

deploy-logs: ## Follow server logs (Ctrl-C to stop)
	scripts/deploy.sh logs

deploy-reset: ## Run reset_demo.py on the server (READY checklist)
	scripts/deploy.sh reset $(ARGS)
