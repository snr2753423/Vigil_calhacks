# AGENTS.md — rules for every coding agent (Claude Code, Cursor, Codex)

## Read first
- `SPEC.md` is the source of truth. Read the sections named in your task before writing code. If something is ambiguous, pick the simplest option consistent with SPEC.md and list the assumption in your final report.
- Work on **one phase at a time** (see `BUILD_PROMPTS.md`). Do not start the next phase unprompted.

## Stack (do not swap)
- Backend: Python 3.12, FastAPI, `uv`, psycopg 3 async, snowflake-connector-python, httpx, numpy, pytest.
- Frontend: Next.js App Router, TypeScript, Tailwind, shadcn/ui, ECharts, Zustand, pnpm.
- Infra: Docker Compose + Caddy (single origin: `/api/*` + `/ws/*` → backend:8000, else → frontend:3000, Caddy on :8080 locally).
- Data: Tiger Cloud (TimescaleDB) + Snowflake. Secrets only in `.env` (never commit; keep `.env.example` updated).

## Hard rules
1. **Sponsor separation:** Tiger does live storage/aggregation/rules. Snowflake does ML + AI. Never use pgai/pgvector/vectorizers in Tiger. Never call a non-Snowflake LLM API.
2. **Time:** store real UTC timestamps only; never write future timestamps. Every scoring/query function takes `as_of`; never call `now()` inside scoring logic.
3. **Snowflake access** goes only through `app/db/snowflake_sql.py` (connector) and `app/db/snowflake_rest.py` (REST: inference, Cortex Search, Cortex Analyst). Count every call and its latency for `/api/stats/snowflake`.
4. **Tiger access** goes only through `app/db/tiger.py`. SQL migrations live in `backend/sql/tiger/` and must be idempotent (`IF NOT EXISTS`, `CREATE OR REPLACE` where possible).
5. **Graceful failure:** if Snowflake is slow or down, the fast tier and UI keep working; AI cards fall back to the deterministic template. No blank screens during a demo.
6. **Deterministic core is tested:** NEWS2 scoring, critical rules, priority formula, hysteresis/suppression, bed-exit state machine, and BLE heart-rate parsing must have unit tests.
7. **Synthetic data only.** Seeded RNG so the demo is reproducible.
8. **No destructive DB actions** except through `scripts/reset_demo.py`, and only when the task asks.
9. Keep UI copy in decision-support language ("check," "consider," "catches decline earlier"); never "diagnose," "prevents death," "replaces staff."

## Commands (create these in phase 1 and keep them working)
- `make dev` — start Caddy + backend + frontend locally (docker compose or local processes).
- `make check` — run `scripts/check_connections.py` (Tiger reachable, TimescaleDB + toolkit versions, Snowflake login, warehouse, a tiny `AI_COMPLETE`, model availability).
- `make migrate` — apply Tiger SQL files in order.
- `make seed DAYS=3` — seed Tiger (and bulk-load Snowflake once Snowflake phase exists).
- `make test` — backend pytest + frontend vitest.
- `make tunnel` — `cloudflared tunnel --url http://localhost:8080` for iPhone HTTPS.

## Definition of done for any phase
- The phase's acceptance checks in `BUILD_PROMPTS.md` pass, and you show the commands/output you used to verify.
- `make test` passes. No TypeScript errors (`pnpm tsc --noEmit`). Lint clean.
- README "Status" section updated with what works and any known issues.
- End with a short report: what you built, how you verified it, assumptions, anything the human must do (keys, accounts, hardware).
