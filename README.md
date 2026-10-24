# Vigil

Vigil watches every resident of a nursing home around the clock, learns each resident's personal "normal," and gives the night shift a live, ranked list of **who to check next and why**. It is decision support that catches decline earlier. It does not diagnose and does not replace staff.

- **Fast tier: Tiger Data (TimescaleDB).** Live ingest, continuous aggregates, hard rules, scored every few seconds.
- **Deep tier: Snowflake.** Anomaly detection against each resident's own baseline, forecasts, Cortex AI explanations with care-plan citations.

`SPEC.md` is the source of truth. `AGENTS.md` has the rules for coding agents, and `BUILD_PROMPTS.md` lists the build phases. All data is synthetic.

## Prerequisites

| Tool | Install | Used for |
|---|---|---|
| uv | `brew install uv` | Python 3.12 backend |
| Node 20+ and pnpm | `brew install node pnpm` | Next.js frontend |
| Caddy | `brew install caddy` | single origin on :8080 for `make dev` |
| Docker Desktop | optional | `make up` (Compose stack) |
| cloudflared | `brew install cloudflared` | HTTPS tunnel for the iPhone pages |

## Setup

```bash
make install                 # uv sync + pnpm install
cp .env.example .env         # then fill in the values below
```

1. **Tiger Cloud.** Paste the service connection string into `TIGER_DATABASE_URL`.
2. **Snowflake key-pair.** Run `make keygen`. It writes `secrets/snowflake_rsa_key.p8` and `.pub` (gitignored, chmod 600) and prints two SQL blocks. It reuses an existing key unless you pass `--force`.
   - Paste both blocks into a Snowsight worksheet, logged in as the user Vigil will connect as, and run them as **ACCOUNTADMIN**. The first block is `backend/sql/snowflake/001_setup.sql`: role `VIGIL_ROLE`, warehouse `VIGIL_WH` (XSMALL, auto-suspend 60 s), database `VIGIL` with schemas `CORE`, `ML` and `DOCS`, the Cortex grant, and cross-region inference. The second block is `ALTER USER … SET RSA_PUBLIC_KEY = '…'`.
   - Set `SNOWFLAKE_ACCOUNT` to `orgname-accountname` (Snowsight → account menu → *Connect a tool*) and `SNOWFLAKE_USER` to your **login name**. Key-pair login matches `LOGIN_NAME`, not `NAME`, and on trial accounts it is often your email. Check it in Snowsight with `SET u = '"' || CURRENT_USER() || '"'; DESC USER IDENTIFIER($u);`.
3. **Verify.** Run `make check`. It prints a PASS/WARN/FAIL table and exits non-zero on any FAIL.
   - Tiger rows: server and TimescaleDB versions, `timescaledb_toolkit` (created if missing), and a `percentile_agg` probe.
   - Snowflake rows: key-pair login, role, warehouse, the `VIGIL` schemas, the cross-region setting, Claude models visible via `SHOW CORTEX BASE MODELS`, a one-line `AI_COMPLETE` with `CORTEX_MODEL`, and whether the ML anomaly-detection class and Cortex Search are visible (actually using them is first exercised in Phases 5–6).

> If `VIGIL` was created earlier by another role, run `GRANT OWNERSHIP ON DATABASE VIGIL TO ROLE VIGIL_ROLE COPY CURRENT GRANTS;` and the same for each of its schemas. Otherwise later phases can't create objects in it.

## Run it

### Option A: local processes (development, hot reload)

```bash
make dev        # → http://localhost:8080
```

This starts three processes, and Ctrl-C stops all of them:

| Process | Address | Command |
|---|---|---|
| backend | 127.0.0.1:8000 | `cd backend && uv run uvicorn app.main:app --reload` |
| frontend | 127.0.0.1:3000 | `cd frontend && pnpm dev` |
| Caddy | :8080 | `caddy run --config Caddyfile` |

Caddy routes `/api/*` and `/ws/*` to the backend and everything else to the frontend. You can run each command above in its own terminal instead. Always browse through **:8080**, because the frontend opens its WebSocket on its own origin.

### Option B: Docker Compose (production-style)

```bash
make up         # build + start caddy, backend, frontend → http://localhost:8080
make logs
make down
```

Compose reads `.env` and mounts `./secrets` read-only into the backend. The `Caddyfile` is the same in both modes. Only the upstreams differ: `BACKEND_UPSTREAM=backend:8000` and `FRONTEND_UPSTREAM=frontend:3000` are set in `docker-compose.yml`. On Vultr, set `SITE_ADDRESS` to your domain **and publish ports 80/443 on the `caddy` service** (Compose only publishes 8080 today); Caddy then handles HTTPS automatically (Phase 10).

### iPhone (HTTPS)

`make tunnel` runs `cloudflared tunnel --url http://localhost:8080`. Open the printed `https://*.trycloudflare.com` URL on the phone. The Next dev server already allows that origin through `allowedDevOrigins`.

## Commands

| Command | What it does |
|---|---|
| `make dev` | Caddy :8080 + backend (reload) + frontend (HMR) |
| `make up` / `make down` / `make logs` | Docker Compose stack |
| `make check` | Tiger + Snowflake connection check (PASS/FAIL table) |
| `make keygen` | Snowflake RSA key pair + the SQL to register it |
| `make migrate` | Apply `backend/sql/tiger/*.sql` in order *(stub until Phase 2)* |
| `make seed DAYS=3` | Seed synthetic history *(stub until Phase 2)* |
| `make test` | backend pytest + frontend vitest |
| `make lint` | ruff + eslint + `tsc --noEmit` |
| `make tunnel` | cloudflared HTTPS tunnel to :8080 |

## Layout

The layout follows SPEC §5. `backend/app/` holds `api/`, `ws/`, `db/` (`tiger.py`, `snowflake_sql.py`, `snowflake_rest.py`), `sim/`, `scoring/`, `deep/` and `devices/`. SQL lives in `backend/sql/{tiger,snowflake}` and scripts in `backend/scripts/`. `frontend/` is a Next.js App Router app. Modules for later phases are placeholders whose docstrings name the phase that fills them in.

## Status

**Phase 1 (scaffold, infra, connection checks): done**

What works:
- **Backend** (FastAPI, Python 3.12, uv).
  - `GET /api/health` returns status, sim state, and which services are configured.
  - `WS /ws/live` sends `hello` on connect, then a `heartbeat` every 2 s with seq, UTC timestamp, viewer count and sim state.
  - `app/config.py` uses pydantic-settings and reads the repo-root `.env`.
  - `app/db/snowflake_sql.py` counts every call per feature with its latency, ready for `/api/stats/snowflake`.
- **Frontend** (Next.js 16, Tailwind 4, shadcn/ui, ECharts, Zustand), dark theme by default. The Rounds Queue page is still a placeholder:
  - header with the facility clock (`America/Los_Angeles`, honors the display offset), Live/Replay badge and connection badge
  - tier legend (icon + label)
  - live heartbeat panel with an ECharts chart of the gaps between heartbeats
  - auto-reconnect with backoff
- **Infra:** Caddy single origin on :8080, Docker Compose + Dockerfiles (Next standalone output), Makefile.
- **Scripts:** `scripts/check_connections.py` and `scripts/gen_snowflake_keypair.py`.
- **Tests:** backend pytest (8: health, WebSocket hello and heartbeats, config, key formatting) and frontend vitest (7: socket URL, backoff, message parsing and reducer, facility clock).

Verified here: `make dev` serves http://localhost:8080 with the heartbeat ticking every 2.0 s through Caddy, at desktop and phone widths, and reconnects after a backend restart. `make test` and `make lint` are clean. `next build` succeeds.

Known issues and not yet verified:
- `make check` against the real accounts: **all PASS**. Tiger: PostgreSQL 18.6, TimescaleDB 2.30.2, toolkit 1.26.0. Snowflake: key-pair login, `VIGIL_ROLE`, `VIGIL_WH`, schemas, cross-region, `claude-sonnet-5` listed, `AI_COMPLETE` answers in ~2 s, ML-function and Cortex Search classes visible (not yet exercised). Cortex AI only works on a trial account after a payment method is added.
- **Cortex REST `POST /api/v2/cortex/inference:complete` returns 403** ("This account is not allowed to access this endpoint") for every model, even though SQL `AI_COMPLETE` works. The same JWT works for the SQL API (`/api/v2/statements`), Cortex Analyst and Cortex Search. If it doesn't clear by Phase 6, ask Snowflake support, or run `AI_COMPLETE` through the SQL API instead (still a Snowflake REST call).
- Docker Compose mode is written but not run here, because the Docker daemon wasn't running on the build machine.
- `make migrate` and `make seed` are stubs until Phase 2.
- `CORTEX_MODEL` defaults to `claude-sonnet-5`. In AWS US West 2 every Claude model needs cross-region inference, which `001_setup.sql` enables. `make check` lists the models your account can actually use.
