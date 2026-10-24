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
| `make migrate` | Apply `backend/sql/tiger/*.sql` in order (idempotent, safe to re-run) |
| `make seed DAYS=3` | Truncate Tiger and regenerate N days of synthetic history ending now (N ≥ 2) |
| `make test` | backend pytest + frontend vitest |
| `make lint` | ruff + eslint + `tsc --noEmit` |
| `make tunnel` | cloudflared HTTPS tunnel to :8080 |

## Layout

The layout follows SPEC §5. `backend/app/` holds `api/`, `ws/`, `db/` (`tiger.py`, `snowflake_sql.py`, `snowflake_rest.py`), `sim/`, `scoring/`, `deep/` and `devices/`. SQL lives in `backend/sql/{tiger,snowflake}` and scripts in `backend/scripts/`. `frontend/` is a Next.js App Router app. Modules for later phases are placeholders whose docstrings name the phase that fills them in.

## Status

**Phase 3 (scoring engine + queue API): done**

What works:
- **Scoring (`backend/app/scoring/`)**, all pure functions of an `as_of` (no `now()` inside):
  - `news2.py`: the full RCP bands, including COPD SpO₂ Scale 2 (with the on-oxygen bands). Inputs are rounded half-up like charted observations. Missing inputs make a partial score. Any single 3, or a total ≥ 5, forces at least HIGH.
  - `rules.py`: the 5 critical rules (HR, SpO₂ ×2 readings, RR in bed, possibly unresponsive, suspected fall) score 100. Fall prevention (night window or force-night/display offset, unless a check was logged from 5 min before the exit onwards) scores 90. Device issues score 30: `heartbeat_agg`/`live_at` liveness > 5 min, or band off > 10 min.
  - `baseline.py`: z vs `resident_baselines` by 4-hour block, with a per-metric σ floor. Tiger fallback severity is min(|z|/4, 1), one-sided for SpO₂ (low) and movement (high).
  - `priority.py`: the exact §7d formula, the +10 multi-signal boost (≥ 3 metrics with |z| ≥ 2), rule/NEWS2/device floors, tiers, and the top-3 factors. Factors are plain-language strings computed from the components (e.g. "New confusion (note 21:30)", "RR 21/min, +37% vs her night normal", "4 bed exits tonight (usual ≤ 2)").
  - `concern.py`: P_concern and NEWS2 consciousness come from the latest note within 12 h. A Snowflake extraction in `ai_insights` is used when present (Phase 6); until then a deterministic keyword fallback applies.
  - `evaluator.py`: runs every 2 s. All 120 residents are read in **one pipelined batch** of 5 queries (raw vitals 15 min, newest impact via a partial index, closed `vitals_1m` minutes for 6 h, events 24 h, note extractions). The `AlertGate` then applies hysteresis (exit 10 below each threshold: HIGH in 60 / out 50), persistence (2 evaluations; rules immediate), 30-min suppression after a check (held at most WATCH unless the score rises ≥ 10 or a new rule fires), and rule episodes. A rule is held for 5 min after it stops firing. A check logged after the episode's latest new rule acknowledges it, so a rule that keeps firing (e.g. unresponsive) is suppressed like a score, and it re-alerts after 30 min. A more severe rule joining the episode (a fall after an unassisted bed exit) alerts even if the resident is already CRITICAL. It writes `priority_snapshots` and emits `alert_raised` / `alert_resolved` / `fall_suspected` events. On `/ws/live` it publishes a slim `queue` (no score breakdown, ~87 KB) as soon as a flagged card's tier/rule/order changes, else every 10 s, plus `alert` messages. New clients get the latest queue on connect.
- **API:** `GET /api/queue[?as_of=]` (without `as_of`: the live gated queue, frozen while the sim is paused; with it: one stateless evaluation on the real facility clock, ignoring force-night and the display offset), `GET /api/residents`, `GET /api/residents/{id}[?as_of=]` (profile, baselines per block, current score, 24 h events), `GET /api/residents/{id}/vitals?metrics=&from=&to=&res=1m|15m|1h` (`time_bucket_gapfill` + `locf`, columnar), `POST /api/checks`, `GET /api/sim`, `POST /api/sim/{pause|start|force_night|inject|simulated_band}`. Injectors: `fall`, `sepsis_drift`, `remove_band`, `bed_exit` (`backend/app/sim/injectors.py`). `/api/health` shows evaluator stats.
- **Migration** `005_scoring.sql`: partial index `vitals (ts DESC) WHERE impact IS NOT NULL`.
- **Tests:** backend 219 passed: every NEWS2 band edge, each rule (positive and negative edges), the formula and floors, factor text, hysteresis/persistence/suppression/rule episodes, keyword concern, and the injectors. `make lint` is clean.

Verified against Tiger here (3-day seed):
- `GET /api/queue?as_of=2026-10-16T02:14:00-07:00` → **#1 Rosa Alvarez (17), CRITICAL 89**: "New confusion (note 21:30)", "RR 21/min, +37% vs her night normal", "4 bed exits tonight (usual ≤ 2)". #2 Henry Lee, HIGH 79 (RR 25, SpO₂ 90 %). Kim (COPD) and Rossi (AFib) stay STABLE.
- Last night replayed statelessly: Patel → "Possibly unresponsive" at ~04:06. Ortiz → 90 (unassisted exit) at 04:06:30, then **100 "Suspected fall"** at 04:06:50, cleared by the 04:11 check. Wright → device issue (band off), never an alarm.
- **Injected fall, resident 5 (daytime, out of bed):** CRITICAL 100 at **+35 s**. That is the first 5-s tick, then the SPEC's ≥ 30 s of stillness after the impact, then ≤ 2 s for the evaluator. **Injected night bed exit** (force-night): CRITICAL 90 plus a `/ws/live` alert with sound at **+10.5 s**. Logging a check (`POST /api/checks`) clears the fall at the next cycle and suppresses for 30 min.
- **Evaluator timing** in the running backend: median **254 ms**, p95 370 ms per cycle, including snapshot writes and the WS publish (one 1.4 s outlier around a restart). A stateless `as_of` evaluation of a compressed chunk takes ~200–300 ms.

Known issues and assumptions:
- **Fall → CRITICAL is ~35 s after injection, not ~5 s:** SPEC §7a requires ≥ 30 s without motion after the impact. At night, a fall-precautions resident's bed exit already makes them CRITICAL (90) within ~10 s.
- With the Tiger z fallback (narrow personal bands) and the keyword note fallback, Alvarez is already CRITICAL from ~23:40. The bookmark story (WATCH 23:40 → HIGH 01:30 → #1 02:14) assumes Snowflake severities (Phase 5) and AI_EXTRACT (Phase 6).
- `priority_snapshots`: every resident every 10 s (`SNAPSHOT_INTERVAL_S`), plus immediately on any tier change, instead of every 2-s cycle (~2 GB/day of jsonb at 2 s).
- Suppression holds the tier at most WATCH (rather than freezing it), so a just-checked resident drops out of the HIGH band until 30 min pass or the score rises ≥ 10. Hysteresis uses a 10-point exit band for every tier (SPEC only specifies HIGH).
- `GET /api/queue?as_of=` is one stateless evaluation (no hysteresis/persistence history). Suppression there uses the snapshot score at the check, if one was recorded.
- Bed exits count toward P_behav at their face value: per the formula, 1 exit against a p95 of 1 is already P_behav = 1.
- `as_of` without an offset is UTC. Stillness (`still_minutes`) only counts minutes with data: a data gap (backend restart, network) resets it, and "heart-rate signal lost" needs the band to still be reporting.
- Fixed in passing: a cancel landing mid-COPY surfaced as psycopg `QueryCanceled` and was swallowed by the live loop's reconnect-on-error loop. That hung uvicorn `--reload`. Both loops now re-raise while the task is being cancelled, and shutdown waits at most 5 s per task.

**Phase 2 (Tiger schema, simulator, seed): done**

What works:
- **Tiger schema** (`backend/sql/tiger/001–004`, applied by `make migrate`, idempotent).
  - Hypertables: `vitals` (wide, 1-day chunks, columnstore `segmentby resident_id`, `orderby ts DESC`), `events` (7-day chunks), `priority_snapshots` (1-day chunks, columnstore).
  - Regular tables: `residents`, `staff`, `devices`, `resident_baselines` (+ view `resident_behavior_baselines`), `ai_insights`, `sync_state`, and `sim_meta` (facility tz, the scripted night and its bookmarks, the baseline holdout, seed stats).
  - Continuous aggregates `vitals_1m → vitals_15m → vitals_1h`, all with real-time aggregation. `stats_agg(hr)` rolls up through all three; `percentile_agg` of 1-minute HR lives in `vitals_15m` and rolls up into `vitals_1h`.
  - Policies: cagg refresh (1 min / 1 min / 15 min), columnstore after 1 day, retention 90 days raw and 2 years for the caggs.
  - Hourly `add_job` baseline job `vigil_resident_baselines` → `refresh_resident_baselines(as_of)`. It computes per resident × 4-hour block (facility time) p05/p50/p95/mean/stddev for hr, rr, spo2, skin_temp and motion, using `rollup`/`approx_percentile` and `percentile_agg`/`stats_agg`. Per night (`tod_block = -1`) it adds bed exits and the longest still period, using `state_agg` + `state_periods`. It reads the trailing 7 days, minus the last 6 h and minus the scripted night. Runs in ~3 s.
- **Simulator** (`app/sim/`).
  - `physiology.py`: seeded per-resident parameters (beta-blocker, AFib and COPD variants), noon-to-noon day plans (bed, nap, bathroom trips, turning, band charging) and staff events (rounds every 2 h walked room by room ±20 min, meals and fluids, BP + core-temp spot checks 2×/day, the occasional note). Residents on fall precautions get up from naps and in the morning with staff (a check logged first), so the fall-prevention rule only fires for genuinely unassisted exits. Vectorized AR(1) signals.
  - `cast.py`: the 120-resident roster (40 per unit) with the SPEC §8 cast pinned, and the scripted "last night" (Alvarez drift + 21:30 note, Lee fluid overload, Patel stillness/decline, Ortiz fall at 04:06, Wright band off 01:00–05:00). Everyone else's bathroom trips that night (Grant included) are call-light assisted; only Alvarez and Ortiz get up alone.
- **Seed** (`make seed DAYS=N`): pause Tiger's jobs (and wait out any in-flight run), truncate via `scripts/reset_demo.py`, then binary COPY over 4 connections (rows are encoded straight from numpy arrays), events, cagg refresh, the baseline job, and compression of every eligible chunk. It prints rows, timings and the compression ratio.
- **Live loop** (`app/sim/live_loop.py`, started by the FastAPI lifespan): backfills from the last simulated row (up to 48 h), then writes one tick for every simulated resident every 5 s. It never writes future timestamps. R-120 is skipped unless `sim_state.simulated_band` is on. `GET /api/health` → `live_sim` shows phase, last tick and rows written. A Postgres advisory lock coordinates it with the seed: the live loop pauses during `make seed`, then resumes from the new history's end, so the backend can stay up. A second lease allows only one live loop per Tiger service (a second backend reports `standby`). It follows the seed and scripted night recorded in `sim_meta`, and never emits an event ahead of its tick.
- **Tests:** backend 51 passed. Covers SQL splitting, the binary COPY encoder against a reference encoder, physiology ranges and cadences, determinism, tick-by-tick vs. batch equivalence, roster/cast attributes, every cast script, and live-loop R-120 gating. `make lint` is clean.

Verified against Tiger here (`make seed DAYS=3`, 122 s):
- **Rows:** 6,220,800 vitals rows (~91k rows/s), 9,750 events.
- **Caggs:** `vitals_1m` 518,520 · `vitals_15m` 34,680 · `vitals_1h` 8,760 rows.
- **Baselines:** 3,625 `resident_baselines` rows covering all 120 residents.
- **Compression:** 2 of 4 chunks, 326.5 MB → 21.8 MB, **15.0×**.
- **7-day query:** hourly HR for all residents from `vitals_1h` takes ~60 ms, against ~3.3 s from raw `vitals`.
- **Live loop:** 119 rows every 5 s, ~3 s behind wall-clock, no duplicate (resident, ts) rows. It paused during a re-seed and backfilled afterwards.
- **Alvarez in Tiger:** 15-min HR 86.8 at 02:00 against her night-block p95 of 62.4. Her max reading before 03:50 is 98; the first HR > 100 is at 03:52.

Known issues and assumptions:
- The scripted night is held out of the Tiger baselines (`sim_meta.baseline_holdout`), the same rule the Snowflake models follow (train on −14…−2 days), so replay is scored out-of-sample.
- If `make seed` is interrupted, `make migrate` (or the next seed) re-enables any Tiger jobs it left paused.
- `still` minute = wrist motion < 6 counts/min (`STILL_MOTION_PER_MIN`, same constant in SQL and Python).
- The timescaledb_toolkit hyperfunctions (`percentile_agg`, `stats_agg`, `rollup`, `state_agg`, `heartbeat_agg`) are all available on this plan, so no plain-SQL fallbacks were needed.
- `make seed DAYS=14` (~29M rows) is expected to take ~6–8 minutes at the measured rate. It has not been run yet.
- Optional `R120_RESTING_HR` seeds the presenter's history from a measured resting HR. Without it, R-120's history uses a simulated 72 bpm.

**Phase 1 (scaffold, infra, connection checks): done**

- Backend: `GET /api/health`, `WS /ws/live` heartbeat, pydantic-settings config, and a Snowflake call counter.
- Frontend: Next.js placeholder Rounds Queue with the live heartbeat.
- Infra: Caddy single origin on :8080, plus Docker Compose.
- `make check`: all PASS against the real accounts (PostgreSQL 18.6, TimescaleDB 2.30.2, toolkit 1.26.0; Snowflake key-pair login, `claude-sonnet-5` via `AI_COMPLETE`).
- Still open:
  - Cortex REST `inference:complete` returns 403 for this account, though SQL `AI_COMPLETE`, the SQL API, Cortex Search and Cortex Analyst work. Revisit by Phase 6.
  - Docker Compose mode is not yet run locally.
