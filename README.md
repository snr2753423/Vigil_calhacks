# Vigil

**Vigil watches every resident of a nursing home around the clock, learns each resident's personal "normal," and gives the night shift a live, ranked list of who to check next and why.** It is decision support that catches decline earlier. It does not diagnose and does not replace staff. All data is synthetic.

Built solo for Cal Hacks 13.0. It targets **[MLH] Best Use of Tiger Data** and **[MLH] Best Use of Snowflake API**.

## The problem

A skilled nursing facility's night shift is often **one aide for 30+ residents**, walking fixed rounds every two hours. Residents decline *between* rounds, and in the elderly that decline is often silent. A urinary infection on its way to sepsis shows up as restlessness, a modest heart-rate rise and a little confusion, not a fever. Fixed alarm thresholds miss it: "HR > 100" never fires for an 87-year-old whose normal is 62. The same thresholds ring all night for residents whose normal is unusual (COPD, beta-blockers, AFib), so staff learn to ignore them.

## What it does

- **Rounds queue:** every resident ranked by a 0–100 priority score with a tier (CRITICAL / HIGH / WATCH / STABLE), the **top 3 reasons** computed deterministically from the data (e.g. "HR 84 (her expected 67–79)", "5 bed exits (usual ≤ 2)"), time since the last check, and an AI summary.
- **Resident page:** charts against the resident's own baseline band, Snowflake's expected range and anomaly dots, a forecast line, a NEWS2 table, and a Snowflake Cortex assessment that quotes the resident's care plan (code status, fall precautions, escalation preferences).
- **Live hardware:** a Polar Verity Sense band streams heart rate from Mac Chrome (Web Bluetooth). An iPhone under the pillow is the bed sensor (DeviceMotion). Getting out of bed at night puts a high-fall-risk resident at #1 within about 4 s.
- **Replay:** last night's recorded data runs through the live pipeline at 30–120× with bookmarks, so the whole story fits in a 3-minute demo.
- **Shift handoff** (SBAR via `AI_AGG`), **voice and text nurse notes** (`AI_TRANSCRIBE` + `AI_EXTRACT`), **Ask the facility** (Cortex Analyst), and an **Under the hood** page that proves every number with a live query.

## How it works: two speeds

```
Simulator (119 residents, every 5 s) ─┐
Polar band → Mac Chrome (Web Bluetooth) ┼─ WS /ws/ingest ─► FastAPI ─► Tiger Data (TimescaleDB)
iPhone /bed/120 (DeviceMotion) ─────────┘                     │        hypertables · caggs · rules
                                                               │  fast tier, every 2 s ─► ranked queue ─► WS /ws/live ─► Next.js
                                                               └─ every 1 min: closed 15-min buckets ─► Snowflake
                                                                  ANOMALY_DETECTION · FORECAST · Cortex (Claude) · Search · Analyst
                                                                  └─► results cached back in Tiger, revealed when "known"
```

- **Fast tier: Tiger Data, seconds.** What is happening now. Hard rules catch emergencies: falls (impact then stillness), extreme vitals, possibly unresponsive (stillness far beyond the resident's own pattern plus a heart-rate drop), and a night-time bed exit by a resident on fall precautions. A partial NEWS2 score, personal-baseline z-scores and alarm-fatigue controls (hysteresis, persistence, 30-min suppression after a check) run over all 120 residents every 2 s.
- **Deep tier: Snowflake, minutes.** What it means and what comes next.
  - Five multi-series `SNOWFLAKE.ML.ANOMALY_DETECTION` models judge every closed 15-minute bucket against **each resident's own learned normal**.
  - `SNOWFLAKE.ML.FORECAST` warns before a NEWS2 threshold is crossed.
  - Claude in **Snowflake Cortex** writes a bedside assessment grounded in the numbers and in care-plan passages retrieved by **Cortex Search**.
- **Graceful failure.** If Snowflake is slow or unreachable, the fast tier and the UI keep working:
  - scoring falls back to Tiger z-scores;
  - AI cards fall back to a deterministic template that still cites the care plan;
  - notes fall back to keyword reading;
  - Ask shows the last real answer, labelled;
  - Under the hood serves its cached inventory.
- **Never in the wrong layer:** no pgai, pgvector or vectorizers in Tiger, and no LLM outside Snowflake.

## How Tiger Data is used

| Tiger feature | What it does in Vigil | File |
|---|---|---|
| Hypertables (`vitals` wide table, `events`, `priority_snapshots`), 1-day chunks | ~2 M rows/day from 120 residents × 10 metrics every 5 s, plus live device rows | `backend/sql/tiger/001_schema.sql` |
| Binary `COPY` over 4 connections | Seeds 10–14 days (23.5 M rows) at ~90k rows/s | `backend/scripts/seed.py`, `backend/app/db/tiger.py` |
| Hierarchical continuous aggregates `vitals_1m → vitals_15m → vitals_1h`, real-time aggregation on | Charts (6 h / 24 h / 7 d), the evaluator's minute view, and the 15-min buckets that feed Snowflake | `backend/sql/tiger/002_caggs.sql` |
| Columnstore compression (`segmentby resident_id`), retention, refresh policies | 14.7× smaller history; raw kept 90 days, aggregates 2 years | `backend/sql/tiger/003_policies.sql` |
| `add_job` hourly job with `percentile_agg` / `approx_percentile` / `rollup`, `stats_agg`, `state_agg` + `state_periods` | Each resident's baseline per 4-hour block (p05/p50/p95), nightly bed exits and longest still period | `backend/sql/tiger/004_jobs.sql` |
| `heartbeat_agg` | Device liveness: band off or silent > 5–10 min is a device task, never an alarm | `backend/app/db/tiger.py`, `backend/app/scoring/rules.py` |
| `time_bucket_gapfill` + `locf` | Gap-free resident charts at 1 m / 15 m / 1 h | `backend/app/db/tiger.py` (`/api/residents/{id}/vitals`) |
| Fast-tier evaluator: one pipelined batch of 5 queries for all 120 residents every 2 s (~0.3–0.4 s) | Rules, NEWS2, baselines, priority, alert gate | `backend/app/scoring/evaluator.py`, `backend/app/db/tiger.py` |
| Advisory locks and leases | One live writer per database, sync / seed / train never overlap, a second backend stands by | `backend/app/db/tiger.py`, `backend/app/sim/live_loop.py` |
| Live device ingest (1-s batched writes; the bed event and its rows in one statement) | Polar band + iPhone → Tiger → #1 in ~4 s | `backend/app/devices/ingest.py` |
| Catalog: `approximate_row_count`, `hypertable_columnstore_stats`, `timescaledb_information.chunks` / `jobs` / `job_stats` | The Under the hood proof panel and the raw-vs-cagg benchmark | `backend/app/api/stats.py`, `backend/app/api/bench.py` |
| Fixed-threshold alarm count over a night of raw readings | Alarm-fatigue comparison | `backend/app/db/tiger.py`, `backend/app/sim/outcome.py` |

## How Snowflake is used

| Snowflake feature | What it does in Vigil | File |
|---|---|---|
| Sync via `write_pandas` (every minute) | Closed `vitals_15m` buckets + events → `VIGIL.CORE` | `backend/app/deep/sync.py` |
| `SNOWFLAKE.ML.ANOMALY_DETECTION`: 5 multi-series models (HR, RR, SpO₂, skin temp, night movement) × 120 series, exogenous `IN_BED_FRAC`, 99 % interval | Judges every resident against their own normal; severity → `P_anom`; the expected band and ◆ dots on charts. Last night is held out of training, so the replay is out-of-sample. | `backend/sql/snowflake/004_models.sql`, `backend/scripts/train_models.py`, `backend/app/deep/anomaly.py` |
| `SNOWFLAKE.ML.FORECAST` (single-series, on HIGH/CRITICAL) | "RR forecast to cross 25 by ~04:15" → `P_trend` and the dashed forecast line | `backend/app/deep/forecast.py` |
| Cortex `COMPLETE` (Claude) with a JSON schema | Bedside assessment: concerns, checks, escalation by code status. Validation drops any citation not in the retrieved care plan and softens diagnostic wording. | `backend/app/deep/assess.py`, `backend/app/deep/wording.py` |
| `AI_PARSE_DOCUMENT` (LAYOUT) + `SPLIT_TEXT_RECURSIVE_CHARACTER` + **Cortex Search** service | 120 care-plan PDFs → 1,200 chunks, searched per resident for every assessment | `backend/sql/snowflake/003_docs.sql`, `backend/scripts/index_care_plans.py` |
| `AI_TRANSCRIBE` + `AI_EXTRACT` | Voice and text nurse notes → confusion, intake, concern → NEWS2 consciousness and `P_concern` | `backend/app/deep/notes.py` |
| `AI_AGG` | SBAR shift handoff | `backend/app/deep/handoff.py` |
| **Cortex Analyst** + semantic model (5 verified queries) | "Ask the facility": natural language → SQL → table + chart | `backend/sql/snowflake/005_semantic.yaml`, `backend/app/deep/analyst.py` |

**Snowflake API surfaces.** All use key-pair JWT auth, and every call is counted with its latency on Under the hood:

| API surface | Endpoint | Used for | Client |
|---|---|---|---|
| Cortex Search REST | `POST /api/v2/databases/VIGIL/schemas/DOCS/cortex-search-services/CARE_PLAN_SEARCH:query` | Care-plan passages for every AI card | `backend/app/db/snowflake_rest.py` |
| Cortex Analyst REST | `POST /api/v2/cortex/analyst/message` | Ask the facility | `backend/app/db/snowflake_rest.py` |
| SQL REST API | `POST /api/v2/statements` (+ status polling, `…/cancel`) | `COMPLETE`, `AI_EXTRACT`, `AI_TRANSCRIBE`, `AI_AGG`, running Analyst's SQL, storing cards and handoffs, the inventory, cancelling queries on shutdown | `backend/app/db/snowflake_rest.py` |
| Cortex Inference REST | `POST /api/v2/cortex/inference:complete` | Tried first for AI cards. This trial account gets a 403 (`003001`), so the same messages go through the SQL API, and the endpoint is retried every 30 min. | `backend/app/db/snowflake_rest.py` |
| Python connector | key-pair JWT | `write_pandas` sync, ML training and inference, `PUT` to stages (care plans, note audio, the semantic model) | `backend/app/db/snowflake_sql.py` |

## Real numbers

Every number below was read from `/under-the-hood` (live queries, not constants) on the 10-day seed on 2026-10-18.

| | |
|---|---|
| Tiger rows | **23.5 M** vitals rows (ingest ~24 rows/s live), 36k events, 650k priority snapshots |
| Compression | **14.7×**: 2.61 GB → 178 MB (10 of 13 chunks in the columnstore) |
| Benchmark | 7-day hourly HR for all 120 residents: **5,158 ms** from raw rows vs **214 ms** from `vitals_1h`, a **24× speedup** (34.7× in an earlier run; it varies with load) |
| Snowflake models | **5 anomaly models × 120 series**, 87,664 results; 1,200 care-plan chunks in Cortex Search |
| Alarm fatigue (replay night) | **903 fixed-threshold alarms** for 110 residents (HR > 100 / < 50, RR > 24 / < 10, SpO₂ < 90, any bed exit) vs **22 Vigil alerts** for 4 residents. Margaret Kim (COPD): 18 fixed alarms, 0 Vigil alerts. |
| Alvarez lead time | Vigil flagged her HIGH at **00:42**: **1 h 18 min** before her 02:00 round (reached at 02:17) and **3 h 20 min** before the first fixed vital-sign alarm (RR > 24 at 04:02). She is **#1 at 02:14**, before anyone knew she was confused. |
| Latency | Bed exit → R-120 **#1 CRITICAL in ~4 s**; Cortex card ~13–16 s; fast-tier cycle ~0.4 s for 120 residents |

## Screenshots

<!-- Add PNGs (1440×900 or so) under docs/screenshots/ with these names; the README links them. -->
| | |
|---|---|
| ![Rounds queue](docs/screenshots/rounds-queue.png) | ![Resident page: Alvarez at 02:14](docs/screenshots/resident-alvarez.png) |
| Rounds queue at 02:14 | Resident page with Snowflake band, anomaly dots, forecast and Cortex card |
| ![Under the hood](docs/screenshots/under-the-hood.png) | ![iPhone bed sensor](docs/screenshots/bed-sensor.png) |
| Under the hood: Tiger + Snowflake proof | iPhone bed sensor |

**Demo:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md) has the 3-minute pitch, the backup plan for each step and likely judge questions. **Deploy:** [DEPLOY.md](DEPLOY.md). **Spec:** `SPEC.md` is the source of truth; `AGENTS.md` has the rules for coding agents; `BUILD_PROMPTS.md` lists the build phases.

**Built with:** Tiger Data (TimescaleDB + toolkit), Snowflake (ML functions, Cortex AI, Cortex Search, Cortex Analyst, REST APIs), FastAPI, psycopg 3, Next.js, Tailwind, shadcn/ui, ECharts, Zustand, Caddy, Docker Compose, Vultr, Polar Verity Sense, an iPhone.

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

Compose reads `.env` and mounts `./secrets` read-only into the backend. The `Caddyfile` is the same in both modes. Only the upstreams differ: `BACKEND_UPSTREAM=backend:8000` and `FRONTEND_UPSTREAM=frontend:3000` are set in `docker-compose.yml`.

On a public server, `docker-compose.prod.yml` is layered on top. It publishes 80/443 and uses `DOMAIN` from `.env` as Caddy's site address (automatic HTTPS) and as `PUBLIC_URL`. `scripts/deploy.sh` does all of this from your Mac; see **[DEPLOY.md](DEPLOY.md)**.

### Live hardware: Polar band + iPhone bed sensor (Phase 7)

The presenter is R-120 Thomas Grant. A Polar Verity Sense streams HR from the Mac's Chrome (Web Bluetooth), and an iPhone under the pillow is his bed sensor. Both send to `WS /ws/ingest`, and the backend writes them to Tiger every second.

1. **Mac setup (once).** Use Chrome. In System Settings → Privacy & Security → Bluetooth, allow Google Chrome. Install cloudflared: `brew install cloudflared`.
2. **Start the stack.** Run `make dev`, then open http://localhost:8080/devices in Chrome.
3. **Polar band.** Put the band on and press its button until the LED shows HR mode (no macOS pairing needed). Click **Connect Polar**, pick "Polar Sense …" in Chrome's list, and click Pair.
   - Within ~2 s, the big number shows your HR, "Skin contact: On the wrist" appears, and "Streamed" counts up.
   - Open R-120 from the Rounds queue. The heart-rate chart shows "Polar live · NN bpm" and the line extends every second.
   - The connection lives outside the page, so it keeps streaming while you navigate. After a full reload, click Connect again.
   - If the band drops (out of range, asleep), it reconnects by itself. The status shows "reconnecting".
4. **Tunnel** (local only; on the Vultr server `PUBLIC_URL` is already `https://$DOMAIN`, see DEPLOY.md). In a second terminal, run `make tunnel` and copy the `https://<words>.trycloudflare.com` URL. Put it in `.env` as `PUBLIC_URL=https://<words>.trycloudflare.com` (no backend restart needed). The `/devices` page shows the QR code within 3 s.
5. **iPhone.** Turn Low Power Mode off, and turn the volume up for the spoken prompts. Scan the QR code with the Camera app and open the link in Safari. Tap **Start sensor**, then **Allow** motion access. The page shows "● streaming" and a live motion meter.
   - If you tapped Don't Allow, close the tab and open the link again. If the "Screen awake" line says no, set Settings → Display & Brightness → Auto-Lock → Never.
6. **Calibrate (~35 s, spoken).** Tap **Calibrate** next to the bed and follow the voice:
   - "Put the phone under the pillow, then step away" (8 s), then "Recording the empty bed" (5 s).
   - "Now lie down" (6 s), then "Stay still" (10 s). Put your head on that pillow.
   - "Now roll over or shift your weight" (5 s).
   - "Calibration done", and you are in bed.

   `/devices` then shows the detection mode:
   - **tilt**: exit in ~2 s. Expected under a pillow.
   - **breathing motion**: ~4 s.
   - **burst + 10 s still**: the SPEC fallback. Move the phone closer to your head and recalibrate.

   The calibration is saved in Tiger (`sim_meta`), so a backend restart keeps it.
7. **Bed-exit test.** In Admin, click **Force night**. Lie down with your head on the pillow and wait for the phone to say you're in bed. Then get up and walk away.
   - The phone says "Out of bed".
   - Within ~5 s, R-120 is **#1 CRITICAL 90**: "Out of bed since HH:MM without staff (1-assist, Morse 70)". A toast and chime appear on the Rounds queue.
   - R-120's AI card shows the template at once, citing "Fall precautions: bed alarm; assist ×1 to bathroom; …". It says "Asking Snowflake Cortex…" and is replaced by the Cortex card ~12–15 s later.
   - Lie back down: the phone says "Back in bed". The fall-prevention rule holds for 5 min, or until you click **Mark checked** on R-120 (Phase 3 alarm-fatigue rule).
8. **Fallbacks.** In Admin, **Bed exit R-120** / **Back in bed** record an exit or return through the same bed-sensor path, without the phone. **Simulated band for R-120** simulates him entirely; while it is on, readings from the real devices are ignored, and `/devices` says so.

## Commands

| Command | What it does |
|---|---|
| `make dev` | Caddy :8080 + backend (reload) + frontend (HMR) |
| `make up` / `make down` / `make logs` | Docker Compose stack |
| `make check` | Tiger + Snowflake connection check (PASS/FAIL table) |
| `make keygen` | Snowflake RSA key pair + the SQL to register it |
| `make migrate` | Apply `backend/sql/tiger/*.sql` in order (idempotent, safe to re-run) |
| `make migrate-sf` | Create the Snowflake `VIGIL.CORE` tables (`backend/sql/snowflake/002_tables.sql`, idempotent) |
| `make seed DAYS=3` | Truncate Tiger, regenerate N days of synthetic history ending now (N ≥ 2), then bulk-load Snowflake |
| `make sf-load` | Re-run the Snowflake bulk load alone (empties `VIGIL.CORE` first) |
| `make train` | Train the 5 Snowflake anomaly models (last night held out), detect since, summarize the cast |
| `make detect` | Re-run Snowflake anomaly detection over last night (or `--from/--to`) and summarize |
| `make prepare` | Prepare last night's replay (anomaly detection if needed, gated scoring pass, Cortex cards); same as /admin → Prepare replay |
| `make care-plans` | Write the 120 care-plan PDFs, upload + `AI_PARSE_DOCUMENT` + chunk them in Snowflake, build Cortex Search (~10 min) |
| `make test` | backend pytest + frontend vitest |
| `make lint` | ruff + eslint + `tsc --noEmit` |
| `make tunnel` | cloudflared HTTPS tunnel to :8080 (copy its URL into `.env` as `PUBLIC_URL`) |
| `make reset` | **Demo to a known-good state** + READY checklist (`ARGS="--simulated-band"`, `ARGS="--retrain"`); see Status → Phase 10 |
| `make deploy-setup` / `deploy-secrets` / `deploy` | Vultr: prepare the server, copy `.env` + key over SSH, ship + rebuild + wait for HTTPS ([DEPLOY.md](DEPLOY.md)) |
| `make deploy-status` / `deploy-logs` / `deploy-reset` | Vultr: health, logs, READY checklist on the server |

## Layout

The layout follows SPEC §5. `backend/app/` holds `api/`, `ws/`, `db/` (`tiger.py`, `snowflake_sql.py`, `snowflake_rest.py`), `sim/`, `scoring/`, `deep/` and `devices/`. SQL lives in `backend/sql/{tiger,snowflake}` and scripts in `backend/scripts/`. `frontend/` is a Next.js App Router app. Modules for later phases are placeholders whose docstrings name the phase that fills them in.

## Status

**Phase 10 (deploy, harden, demo assets): done in code, verified locally; the Vultr deploy waits for your server**

What works:
- **Deploy** (`docker-compose.prod.yml`, `scripts/deploy.sh`, `make deploy*`, [DEPLOY.md](DEPLOY.md)):
  - `DOMAIN` in `.env` is the one setting. It becomes Caddy's site address (Let's Encrypt HTTPS; `<ip>.sslip.io` for the practice run, a real domain at Cal Hacks) and `PUBLIC_URL=https://$DOMAIN`, so the iPhone QR code needs no tunnel. Ports 80/443 (+ UDP 443 for HTTP/3) replace 8080.
  - `deploy.sh` subcommands:
    - `setup`: Docker, 4 GB swap, ufw 22/80/443.
    - `secrets`: `scp` of `.env` + the key, `chmod 600`.
    - `push`: rsync of the working tree, excluding `.git`, `.env*`, `secrets/`, `*.p8`, `*.pem`, `node_modules` and caches. Then `compose up --build` and a wait for `https://$DOMAIN/api/health`.
    - also `status`, `logs`, `reset`, `ssh`, `stop`.
  - Compose additions: log rotation (10 MB × 3), a backend healthcheck, and a 30 s stop grace period. uvicorn runs with `--timeout-graceful-shutdown 5`, so open WebSockets can't hold up shutdown. Caddy compresses responses (`encode zstd gzip`): the queue goes from 177 KB to 18.5 KB.
- **`scripts/reset_demo.py`**, run with no command (`make reset`, or `scripts/deploy.sh reset` on the server). It prints a READY checklist with PASS / WARN / FAIL per item and exits 1 on any FAIL.
  1. **Clean** what the live session left since the seed ended, in Tiger and Snowflake CORE (plus note audio, and handoffs for later shifts):
     - **R-120:** all events (phone and admin bed-exit tests, alerts), snapshots, cards, forecasts and extractions.
     - **Residents with typed or spoken notes:** the notes and everything they caused (what `remove-notes` did).
     - **Mark-checked clicks:** the check events.
     - **Kept:** simulated history and device vitals, which are the Polar band's future baseline.
  2. **Controls**, through the running backend: replay stopped and `POST /api/sim/reset`, then the new `POST /api/sim/reload`. It resets the alert gate for cleaned residents and reloads the queue headlines, the handoff cache and the evaluator context, so no restart is needed. `--simulated-band` turns R-120's simulation on.
  3. **Models**: valid when trained for this seed and all 5 exist in `VIGIL.ML`. If only the metadata is missing, it re-registers them (`train_models.py --no-train`). It retrains only with `--retrain`.
  4. **Replay**: valid when prepared for this seed and finished, made with the current models, with every card made and every step in `replay_snapshots`. Otherwise it runs Prepare, which redoes only what's missing. It also checks the story: Alvarez #1 at 02:14.
  5. **Live checks**:
     - Cortex Search: R-120's "bed alarm" chunk.
     - Cortex Analyst: all 5 suggested questions, one at a time; each answer is saved as /ask's offline fallback.
     - A real Cortex card for Alvarez at 02:14, built by the new `Assessor.generate`: context + Search + COMPLETE + validation, not stored.
     - The recorded night's handoff.
  6. **Warm**: the slow API reads, then every page twice through the public origin.
  - Measured against the real accounts: **11/11 PASS in 28 s**:

    ```
    PASS  Tiger             TimescaleDB 2.30.2 · 23.5 M vitals rows · 10-day seed (seed 1337) · newest row 5 s ago
    PASS  Sim controls      0 injections cleared · night mode off · simulated band off · 1 gates reset
    PASS  Backend           http://127.0.0.1:8000 · mode live · live loop streaming · evaluator running · warm-up 2.0 s
    PASS  Snowflake         key-pair login (connector + REST JWT) · VIGIL_WH · 1.4 s
    PASS  Anomaly models    5 models × 120 series · trained 10-17 11:37 on 9.1 days (last night held out)
    PASS  Replay prepared   night of 2026-10-16 · 286 steps · 22 alerts · 27 AI cards · Alvarez #1 HIGH 63 at 02:14
    PASS  Care-plan search  Cortex Search REST · R-120 → "- Fall precautions: bed alarm; assist ×1 to bathroom; …" (384 ms)
    PASS  Cortex Analyst    5/5 suggested questions answered in 15.9 s · "Which residents had more than 3 bed ex…" → 16 rows
    PASS  Cortex card       claude-sonnet-5 via sql_api · R-17 at 02:14 · "Rising HR/RR and low SpO2 overnight, check Rosa now" · 3 citations (14 s)
    PASS  Shift handoff     night of 2026-10-16 · SNOWFLAKE.CORTEX.AI_AGG · flags Alvarez, Patel, Ortiz, Lee, Wright
    PASS  Pages             9/9 pages via http://localhost:8080 · slowest /resident/17 25 ms
    ```

    The first run cleaned R-120's Phase 7 device tests: 32 events, 5,152 snapshots and 6 cards. The earlier R-23 / R-50 test voice notes had already been removed in Phase 9; the same notes are now caught automatically.
- **Snowflake queries are cancelled on shutdown** (the Phase 9 known issue):
  - Every connector connection is registered, and the lifespan's shutdown runs `SYSTEM$CANCEL_ALL_QUERIES(<session>)` for each one through the SQL API. That runs in cloud services, so it isn't queued behind what it cancels. It also cancels SQL API statements still being polled.
  - A statement the client gave up on (timeout) is now cancelled too.
  - Backend sessions set `ABORT_DETACHED_QUERY`, so a crash or `kill -9` doesn't leave a query running.
  - Measured: `CALL SYSTEM$WAIT(60)` was cancelled 0.5 s after the call. In Snowflake's query history, the `DETECT_ANOMALIES` runs in flight at each dev hot-reload during this phase ended `FAILED_WITH_ERROR` (cancelled) instead of stacking.
- **Live detection no longer starves the interactive calls**:
  - Query history showed the 5 parallel `DETECT_ANOMALIES` calls (10 queries for ~4.5 min of every 15) saturating XSMALL `VIGIL_WH`. Analyst's SQL queued 46 s and timed out twice, and the inventory queued 30 s.
  - Live detection on the shared warehouse now runs at most 2 models at a time (`ANOMALY_DETECT_CONCURRENCY`, default 2). Optionally it gets its own warehouse (`SNOWFLAKE_DETECT_WAREHOUSE`; all 5 in parallel there). Prepare and `make train` are unchanged.
  - After the change: Analyst answered 5/5 in 15.9 s while detection was running. A detection cycle takes ~7.5 min (each model ~140 s alone vs ~280 s when all five share the warehouse), so results land ~12 min after a bucket closes.
- **Standby for a second backend**: when another backend holds the live-writer lease (e.g. the server while `make dev` runs), this one still serves its pages, but its evaluator writes no snapshots or alerts. Automatic sync, detection, Cortex cards and forecasts are left to the primary. Verified with the production containers next to `make dev`: live loop and sync `standby`.
- **Startup warm-up** (`app/warmup.py`, `/api/health` → `warmup`): in the background it fills the outcome cache, the Snowflake inventory, the recorded night's handoff and the Analyst semantic-model upload (2.0–2.3 s). The Snowflake inventory is now served stale-while-revalidate, so the panel never waits on Snowflake.
- **Offline-safe fallbacks for every Snowflake feature**:
  - scoring: Tiger z-scores;
  - AI cards: the deterministic template, with care-plan lines from the roster;
  - notes: keyword reading;
  - forecasts: none, so P_trend is 0;
  - handoff: the deterministic handoff;
  - **Ask: the last real answer to the same question, labelled with its time** (new: stored in Tiger `sim_meta`);
  - Under the hood: the cached inventory with the error shown;
  - replay never calls Snowflake.
- **Nav menu** (`components/nav-menu.tsx`, `lib/nav.ts`): role-aware. The header shows the role's pages (CNA/LPN: Rounds, Devices; Charge RN: Rounds, Handoff, Ask; DON: Rounds, Ask, Handoff, Under the hood), and the rest go under **More**. On phones it is one **Menu** button with "For <role>" and "More" groups. The phone header is two rows: logo, menu and clock, then status.
- **Hidden demo shortcuts** (`components/demo-shortcuts.tsx`, `lib/shortcuts.ts`):
  - **?** opens the panel.
  - **Space/P** play/pause (starts the replay from live). **1–9** jump to a bookmark (from live: opens the replay there, paused). **S** speed. **L** back to live.
  - **N** force night. **B / Shift+B** R-120 bed exit / back in bed. **G then Q/R/H/A/D/U/M** go to a page.
  - Keys are ignored while typing and on the iPhone bed page. A small status pill confirms each action.
  - Verified in the browser: **3** → 02:14, Alvarez #1 HIGH 63. **7** + Space → Ortiz fall, then Patel. **L**, **N**, **B** → R-120 **#1 CRITICAL 90** "Out of bed since 18:44 without staff (1-assist, Morse 70)" ~6 s after the key press.
- **Production build check** (Docker Desktop, images built locally: backend 1.45 GB, frontend 313 MB): the prod-image stack on :8090 next to the dev stack was healthy. All 8 pages returned in 7–105 ms (DOMContentLoaded ~30 ms), with **no console errors** on any page.
- **Docs**: the README now opens with the Devpost write-up (problem, two tiers, Tiger and Snowflake feature → file tables, every Snowflake API surface, the real numbers, screenshot placeholders). [DEMO_SCRIPT.md](DEMO_SCRIPT.md) has the 3-minute pitch timed to the bookmarks and the live R-120 moment, a backup plan per step, and judge Q&A.
- **Quieter logs**: the connector's multi-line IMDS warning on every login (no cloud metadata in Docker or on Vultr) is silenced.
- **Tests**: backend **430** passed:
  - shutdown cancel (sessions, handles, nothing-to-cancel) and cancelling a statement given up on;
  - Ask's offline fallback (served only for the same question without history) and what is remembered;
  - standby;
  - the detection concurrency cap (peak 2 vs 5);
  - the READY report's exit code and the backend check.

  Frontend **66** passed: nav per role, shortcut key mapping, typing/modifier guard, speed cycle. `make lint` is clean.

Known issues and what you must do:
- **The Vultr server isn't created yet** (it needs your account). DEPLOY.md steps 1–4 are by hand: account, SSH key, Deploy, and `DEPLOY_HOST` + `DOMAIN` in `.env`. Then run `scripts/deploy.sh setup`, `secrets`, `push`, `reset`.
  - The public-URL acceptance checks (full demo over HTTPS, the iPhone bed page over that URL) are untested until then.
  - `deploy.sh` setup/secrets/push have not run against a real server. The rsync file list and the merged prod Compose config were checked locally.
- **Sim controls live in the backend's memory** (force night, simulated band). Every redeploy or restart resets them, so run `scripts/deploy.sh reset --simulated-band` after a push until the Polar band works (it arrives Monday).
- `reset_demo.py` does not undo injected scenarios' vitals. Admin injections end at reset, and their rows age out of the 6 h scoring window.
- Live detection under the concurrency cap takes ~7.5 min per bucket. To get results sooner for the demo, set `SNOWFLAKE_DETECT_WAREHOUSE=VIGIL_ML_WH`. Note it is MEDIUM (4 credits/h while running, ~25 % duty), so switch it back afterwards, or create an XSMALL `VIGIL_DETECT_WH` as ACCOUNTADMIN.
- Screenshots are placeholders (`docs/screenshots/*.png`).
- The app has no login (SPEC). Anyone with the public URL can use Admin, so destroy the server after the event.

**Phase 9 (forecast, nurse notes, shift handoff, Ask the facility): done**

What works:
- **Forecast** (`app/deep/forecast.py`, `app/scoring/trend.py`):
  - On an alert into HIGH/CRITICAL, and every 30 min while the resident stays there, three single-series `SNOWFLAKE.ML.FORECAST` models are trained in parallel (RR_AVG, SPO2_MIN, HR_AVG over the trailing 4 h of closed `VITALS_15M` buckets, `evaluate: FALSE`). Each then runs `!FORECAST` for 16 periods with a 90 % interval and is dropped.
  - Results go to `CORE.FORECAST_RESULTS` and Tiger `ai_insights` (kind `forecast`, as_of = when known).
  - Forecasts run on `SNOWFLAKE_TRAIN_WAREHOUSE` (VIGIL_ML_WH) when it is set. Measured: training ~50–110 s per metric, prediction ~10–15 s. On the shared XSMALL warehouse, three trainings queued AI_EXTRACT past its timeout.
  - **P_trend** (SPEC §7d): the first forecast bucket inside a NEWS2 3-point zone (RR ≥ 25 / ≤ 8, SpO₂ ≤ 91 or ≤ 83 on scale 2, HR ≥ 131 / ≤ 40) gives 1 within 2 h and 0.5 within 4 h. It is read from the newest forecast ≤ 60 min old.
    - A threshold within MIN_GAP of the last value does not count (RR 1/min, SpO₂ 2 %, HR 10 bpm). Lee's SpO₂ minimum sat at 91–92 %, so without the gap every forecast "fell to 91 %". RR is a smooth average, so 1/min is a real step (Lee at 23.4 heading for 25, Patel at 9 heading for 8).
    - Crossings, P_trend and the callout are recomputed when read, so stored forecasts follow rule changes.
    - The new factor chip reads "RR forecast to cross 25 by ~04:15". "Why this score" names the metric and the time.
  - **Charts**: HR / RR / SpO₂ get a dashed amber forecast line with its 90 % band for the next 2 h past the last reading, plus a red "NEWS2 +3" threshold line. A callout above the charts reads "RR forecast to cross 25 by ~04:15 · Snowflake ML.FORECAST · made 02:30 from the last 4 h". Charts follow the replay cursor.
  - The AI assessment context lists each forecast metric's last value, its 2-h value with range, and its crossing.
- **Nurse notes** (`app/deep/notes.py`, `POST /api/notes`, JSON or multipart):
  - Text goes to `AI_EXTRACT` through the SQL REST API: confusion_change, oral_intake, pain, urinary symptoms, fall, breathing, mood, fever/warm, concern level and a one-line summary.
  - Voice: the browser records (Chrome `audio/webm;codecs=opus`, iPhone Safari `audio/mp4`), the backend `PUT`s the file to `@CORE.NOTE_AUDIO`, then runs `AI_TRANSCRIBE`, then `AI_EXTRACT`. A failed transcription stores nothing and asks for a typed note.
  - The extraction is stored in Tiger `ai_insights` (kind `note_extract`) and Snowflake `CORE.NOTE_EXTRACTS`. New or worse confusion → NEWS2 consciousness 3 and P_concern 1. The keyword reading remains the fallback when Snowflake fails, and it scores the note until AI_EXTRACT lands (waited up to 8 s; later arrivals come over `/ws/live`).
  - `concern_level` is Vigil's deterministic reading of the extracted findings. Measured: AI_EXTRACT's own overall rating called a wristband note "med" and "slept well" "low". Its rating is kept as `ai_concern_level` and can only raise a finding-backed note to high.
  - UI: a **Voice note** button with recording state and timer (stops at 60 s), then the transcript and chips ("Confusion: worse → NEWS2 consciousness +3", "Intake: reduced", concern level), with the source shown. The timeline labels voice notes.
  - Prepare replay re-extracts the night's 7 non-routine notes (stamped 30 s after each note). Alvarez 21:30 → concern low, no confusion. **Alvarez 02:20 → confusion worse**, concern med.
- **Shift handoff** (`app/deep/handoff.py`, `GET/POST /api/handoff`, `/handoff`):
  - Gathers the shift from Tiger: tier changes and peak (`replay_snapshots` for the prepared night, else `priority_snapshots`), alerts, checks, non-routine notes with their extractions, falls, bed exits, device issues ≥ 30 min, and forecast callouts.
  - Two `AI_AGG` calls run in parallel (SQL REST API, rows sent as JSON and FLATTENed):
    - the sections (critical events, watch next shift, resolved, follow-ups) over one SHIFT row plus one RESIDENT row per flagged resident;
    - one SBAR line per resident over that resident's event records, `GROUP BY` resident. Asked for both at once, AI_AGG left the SBAR out.
  - Output is validated and softened. A missing SBAR gets the deterministic one. Device issues are listed from the data. A deterministic handoff covers Snowflake being down.
  - Stored in `CORE.HANDOFFS`.
  - UI: shift picker (defaults to the recorded night), Generate, the sections and SBAR table, "What Snowflake was given", and **Print** (light, chrome-free print styles).
- **Ask the facility** (`app/deep/analyst.py`, `sql/snowflake/005_semantic.yaml`, `POST /api/ask`, `/ask`):
  - Semantic model over RESIDENTS, VITALS_15M, EVENTS, CHECKS, ALERTS and NURSE_NOTES, with facility-time dimensions (`night_of` = the evening's date, `is_night` = 22:00–07:00) and the 5 verified queries.
  - Uploaded with `PUT` to `@CORE.SEMANTIC/vigil.yaml` on first use and whenever the file changes.
  - Flow: Cortex Analyst REST `message`, then a read-only guard (one SELECT/WITH, no write keywords), then the SQL REST API (≤ 500 rows).
  - The chart is chosen from the result's shape: line over dates, bar, or bars stacked by a series column (tier colours).
  - UI: suggested-question chips, the interpretation, a "verified query" badge, chart, table, collapsible SQL, timings, and follow-up questions with history.
- **Decision-support wording** (`app/deep/wording.py`):
  - Rewrites firm wording in Cortex text: "infection likely" → "infection possible", "likely UTI" → "possible UTI", "has early sepsis" → "may have early sepsis", "probably" → "possibly", "a diagnosis of" → "possible".
  - Puts "possible" before a bare condition name with no hedge.
  - Rejects what it cannot fix (e.g. "Diagnosis: …"); the card then falls back to the template.
  - Care-plan quotes are never touched.
  - The assessment prompt now bans "likely / probably / diagnosis / confirms / indicates".
  - `MAX_TOKENS` went 1500 → 3000: the model's reasoning counts toward it, and three of Lee's cards were cut off mid-JSON.
- **Prepare replay** now runs: detection → notes (AI_EXTRACT) → gated pass → forecasts where live would ask → gated pass again with P_trend in place (repeated until no new forecast is asked for) → cards. A new admin option, "redo forecasts and note extraction", forces those two steps. Replay still never calls Snowflake.
- **Counted calls**: every new call shows on `/under-the-hood` under its API surface.
  - SQL REST API: `note_extract`, `transcribe`, `store_note_extract`, `handoff_agg`, `handoff_sbar`, `store/read_handoff`, `analyst_exec`.
  - Cortex Analyst REST: `cortex_analyst`.
  - Connector: `forecast.train/predict/inputs/drop/store`, `notes.put_audio`, `analyst.put_semantic_model`.
  - The inventory adds forecasts, note extractions and handoffs.
- **Tests**:
  - Backend **415** passed: Lee's new curve (crossing ~03:00, unchanged from 04:30), P_trend lead times, zones, MIN_GAP, COPD scale and callout; the replay forecast plan; extraction normalising and yes/no answers; the notes API (typed, voice, transcription failure, extraction fallback, pending); wording rewrites and rejection; handoff windows, rows, deterministic sections and validation; Analyst's read-only guard, chart choice, reply parsing and the YAML's verified questions; the forecast insights endpoint.
  - Frontend **59** passed: recorder format, extract chips, Ask chart options, forecast overlay and freshness.
  - `make lint` is clean.

Verified against the real accounts (`make dev`, in-app browser):
- **Cortex Analyst REST is enabled** on this trial account (unlike inference:complete). All 5 verified questions answer in ~2.5–3.5 s plus ~0.5 s of SQL, each through its verified query. Every table matches an independent query on Tiger:
  - 16 residents with > 3 bed exits last night (night of 10-17).
  - Memory Care night HR 61.4–62.1 over 7 nights.
  - Least checked overnight: Thomas Grant 0 (live R-120, no sim checks), then 3 each.
  - Alerts by unit/tier match (Tiger has one more East HIGH: the test note below, raised after the query).
  - RR > 15 % rise: no rows; the largest rise in the last 24 h is 1 %.
  - A free-form "How many bed exits per unit last night?" gave 81 / 79 / 62, matching the 222 total.
- **Spoken note** "She seems more confused tonight and barely drank." (m4a, R-23 Betty Scott, live), 4.3 s in total:
  - AI_TRANSCRIBE returned the exact sentence in 2.7 s; AI_EXTRACT gave confusion worse, intake reduced, concern med.
  - **NEWS2 consciousness 0 → 3, score 9.7 STABLE → 60 HIGH**: a single 3-point parameter forces HIGH.
  - The alert then triggered a live forecast (112 s on VIGIL_ML_WH) and the card "New confusion noted; check mental status and fluids".
  - Chrome's webm/opus (re-encoded in the browser pane, R-50) also transcribed exactly, in 4.6 s.
- **Handoff, night of 10-16** (AI_AGG, ~10 s) names Ortiz (suspected fall 04:06, found on the floor), Patel (CRITICAL unresponsive / RR ≤ 8, comfort care), Alvarez (new confusion, HIGH at end, urinalysis pending), Lee (RR 26, SpO₂ 89 %, provider call) and **Doris Wright: wristband not worn 01:10–04:58 (3 h 48 min)**.
- **Replay**, read from the running replay engine (`POST /api/replay/start`, then each resident's card at the cursor):

  | Time | Rosa Alvarez (17) | Henry Lee (42) |
  |---|---|---|
  | 23:12 | WATCH 44, #1 (first sustained deviation) | STABLE |
  | 23:48 | WATCH 51, #2 | HIGH 60, #1 (SpO₂ 91 % dip; brief) |
  | 00:42 | HIGH 61, #1 (brief, until 00:56) | WATCH |
  | 01:12 | WATCH 48, **#1 from here to 02:26** | WATCH, #2 |
  | 01:34 | HIGH 60, #1 | WATCH 42, #2 |
  | 01:54 | HIGH 60, #1 | HIGH 60, #2 (briefly), forecast asked: no crossing |
  | **02:14** | **HIGH 63, #1** ("Rising HR/RR and low SpO2 over past hours; check Rosa now") | **WATCH 53, #2** (RR 22, SpO₂ 93 %) |
  | 02:17–02:20 | late 02:00 round reaches her → WATCH (check), confusion note 02:20 → 72 | |
  | **02:28–02:32** | WATCH 73, #2 | **HIGH 60–66, #1**; the 02:28 forecast is known at **02:30: "RR forecast to cross 25 by ~04:15"** (P_trend 1) |
  | 02:34 | HIGH 73, #1 again; CRITICAL 81 at 02:38 | HIGH 66, #2 |
  | ~03:00 | CRITICAL 86–100, #1 | RR reaches 25 (25.3 in the 03:00 bucket), HIGH 72, #2 |
  | 03:08–03:18 | WATCH 90–95 (checked), #2 | HIGH 72, #1 |
  | 03:20 / 03:25 | WATCH 95, #1 | RN assessment, then the O2 / head-of-bed note: WATCH 69 |
  | 03:52 | WATCH 95, #2 | HIGH 79, #1 (RR 26) |
  | 04:38 | CRITICAL 94, #1 | WATCH 59, #3, improving after O2 |

  The same pass gives 22 Vigil alerts for 4 residents (Patel CRITICAL from 04:02, Ortiz 04:06 / fall 04:08).
- **Prepare**: the first run with forecasts took ~22 min (27 forecasts at ~1 per minute). After Lee's re-script it took 1,657 s, including detection, 24 forecasts on the 4 h window and **27 of 27 Cortex cards regenerated**, every headline in decision-support wording (e.g. "Rising HR/RR and low SpO2 over past hours; check Rosa now"). A run with everything cached takes ~5 min.
- **Lee's re-script** (`scripts/reset_demo.py rescript --resident 42`):
  - Regenerated his seeded history with the seed's RNG. The 6 h before the window matched Tiger exactly (4,320 rows).
  - Replaced only his rows from noon before the night to the seed's end: 16,061 vitals rows, 26 events and 89 Snowflake VITALS_15M buckets.
  - Then re-ran detection for the night. **No re-seed and no retraining**: the night is held out of training and baselines.
  - Rows after 04:30 are unchanged, so the live loop's rows after the seed still fit.

Known issues and assumptions:
- **Lee's script moved later (SPEC §8, §4 updated).** With P_trend live, his old climb (RR 25 at 02:15) and its forecast held him above Alvarez at 02:14.
  - Now his RR is gentle until 01:45, steps to ~22 by 02:15 and crosses 25 around 03:00. SpO₂ falls to 90 by ~03:15. The RN assessment moves to 03:20 and the O2 note to 03:25.
  - Bookmarks: "Lee forecast warning" moved 01:30 → **02:30**; the 02:14 bookmark is now "Alvarez at #1" (she is #1 from 01:12, no longer "reaching" it).
- **SPEC deviations**:
  - Forecasts train on 4 h, not 48 h. RR exists only in bed, so 48 h is three separate nights, and the forecast stayed flat at 20.3 while Lee climbed. At 02:28, 6, 8 and 12 h windows all returned a flat forecast (21.8); only 4 h followed the climb. The cost is over-extrapolation late in a steep rise: Lee's 03:00 forecast projects RR ~31 within 2 h. `FORECAST_TRAIN_HOURS` changes it.
  - Forecasts run 16 periods (4 h); the chart shows SPEC's 2 h, and the extra 2 h make P_trend's 0.5 reachable.
  - `concern_level` is derived from the extracted findings (above).
- **Forecast timing is coarse**: Lee's 02:30 forecast says 25 "by ~04:15", while it is reached at ~03:00. FORECAST extrapolates the 4-h trend from data ~30 min old (sync lag), and asks only happen on HIGH entries and every 30 min.
- **Dev hot-reload stacks anomaly detection**: each backend reload starts a new `DETECT_ANOMALIES` run while the old process's queries keep running in Snowflake. During this build four runs overlapped on XSMALL and queued every interactive call. `ANOMALY_DETECT_ENABLED=false` was set in `.env` while building and has been removed. The running dev backend picks the restored setting up on its next restart or reload. A real fix (cancel server-side queries on shutdown) is not built.
- **Test notes removed**: `reset_demo.py remove-notes --resident 23 50 --since 2026-10-18T19:55Z` removed both voice notes, their alerts, snapshots, extractions, forecast and card from Tiger and Snowflake, plus the audio files. Betty Scott is back to STABLE 12.5.
- `reset_demo.py clear-forecasts` empties last night's forecast cache. I used it when switching to the 4 h window, so stale 12-h forecasts can't surface in replay.
- Fixed: the last card of a prepare run lost its Snowflake copy (`CORE.AI_ASSESSMENTS`) because the script closed the HTTP client under the background write. Prepare now waits for those writes.
- A live forecast lands ~1.5–2 min after the alert. The card made at the alert has no forecast; the next card includes it.

**Phase 8 (replay mode + Under the hood): done**

What works:
- **Replay** (`app/sim/replay.py`, `/api/replay/{start|pause|seek|speed|stop}`, `GET /api/replay`):
  - A cursor over last night (22:00–06:30) at 30/60/120×. Every 2 min of replay time, the real evaluator scores all 120 residents `as_of` the cursor through its own AlertGate (hysteresis, persistence, suppression). At 60× that is one evaluation every 2 s, the live cadence.
  - Steps lie on a fixed grid from 21:00. A seek (a time, "HH:MM", or a bookmark) snaps to the grid and seeds the gate from the `replay_snapshots` the prepare pass wrote, so a replay from any point shows the same tiers as one from the start.
  - `/ws/live` carries the replay's queue, its `alert`s (toasts and the CRITICAL chime) and a `replay` message with the cursor each step. The header shows a **Replay** badge and the replay clock. A banner on every page says "Replaying last night's recorded data through the live pipeline." and has play/pause, speed, bookmarks and **Back to live**.
  - Live keeps running underneath: the simulator writes, the live evaluator scores and stores, but only the replay is published. Back to live republishes the live queue at once.
  - **Never calls Snowflake.** Anomaly results come from Tiger `ai_insights` (each stamped with when live would have known it). Queue headlines and the resident page's card are the prepared Cortex cards with `as_of ≤ cursor` (≤ 60 min old). Refresh is hidden and `POST /api/explain` answers from the cache. Checks and notes are refused (409) and shown read-only.
  - The resident page and its charts follow the cursor: score breakdown, events and vitals all end at the replay time.
- **Prepare replay** (`app/sim/prepare.py`, `make prepare`, `/admin` → Prepare replay with progress):
  1. `ANOMALY_DETECTION` over the night (from 20:15) for all residents. Skipped when the night already has results from the current models, unless "re-run anomaly detection" is ticked.
  2. The gated pass over the replay grid: 286 steps, upserting `replay_snapshots` (new table, `007_replay.sql`) and recording each resident's tier changes, every alert, and the cast's ranks.
  3. The forecast hook `deep/forecast.prepare_replay_forecasts` gets every resident entering HIGH/CRITICAL (22 on this night). It is a no-op until Phase 9.
  4. Cortex cards at every bookmark and every escalation into HIGH/CRITICAL. A score-driven escalation within 10 min of that resident's previous card shares it; a new rule always gets its own. Capped at 40. Cards already cached are kept unless "regenerate" is ticked.
  
  The summary is stored in `sim_meta.replay_prepared` for the current seed. Replay refuses to start without it ("Run Prepare replay first").
- **Bookmarks** (`cast.BOOKMARKS`, read at runtime, so no re-seed is needed; SPEC §4 updated): 23:12 Alvarez first deviation (WATCH) · 01:30 Lee forecast warning · 01:34 Alvarez reaches HIGH · 02:14 Alvarez reaches #1 · 02:20 Alvarez confusion noted · 03:20 Patel immobility · 04:05 Ortiz fall.
- **`/under-the-hood`**: every number comes from a query.
  - **Tiger** (`GET /api/stats/tiger`, one pipelined round trip): ingest rows/s from the last minute by source; `approximate_row_count` for vitals, events, snapshots and the three caggs; `hypertable_columnstore_stats` (ratio, before/after); chunks; caggs with real-time aggregation; every job with `job_stats`.
  - **Benchmark** (`POST /api/bench/hourly_hr`): 7-day hourly HR for all 120 residents, raw vs `vitals_1h`, timed back to back.
  - **Snowflake** (`GET /api/stats/snowflake`): calls grouped by API surface (SQL REST API, Cortex Search REST, Cortex inference REST, Cortex Analyst REST, Python connector), with each feature's calls, errors and average latency. Totals are cumulative across processes: every process adds its counts to the new Tiger table `snowflake_calls` (`006_stats.sql`; the backend every 30 s, scripts on exit).
    - An inventory is read from Snowflake itself through the SQL REST API: `SHOW SNOWFLAKE.ML.ANOMALY_DETECTION`, `SHOW CORTEX SEARCH SERVICES`, and counts of results, series, synced rows, assessments and care-plan chunks. It is cached 5 min, and a failure is retried after 1 min.
    - The newest detection run is kept in `sim_meta.anomaly_last_run`.
  - **Outcome** (`GET /api/stats/outcome`): fixed-threshold alarms over last night's raw readings (HR > 100/< 50, RR > 24/< 10, SpO₂ < 90, any bed exit; readings < 5 min apart are one alarm) vs the alerts the gated pass raised. Alvarez's lead time, the cast table and her notes.
  - An SVG architecture diagram of the fast tier vs the deep tier.
- **Tests**: backend **343** passed. New ones cover the grid and snapping, bookmarks, card reveal by `as_of`, the assessment plan (dedupe, rule exemption, cap), outcome metrics and lead times, call-count drain/restore and API surfaces, and the engine against a faked Tiger: needs prepare, seek seeds the gate, alerts on steps but not on seeks, speeds, no Cortex in replay, writes refused. Frontend **47** passed (replay clock, `replay` messages, toasts cleared on seek and on back to live). `make lint` is clean.

Verified against the real accounts (`make dev`, in the in-app browser):
- **Prepare replay from /admin** with "re-run anomaly detection": 544 s in total.
  - Detection: 22,822 results and 511 flagged resident-buckets in 258 s (HR 200 s … movement 252 s).
  - Gated pass: 286 steps at 363 ms each, 23 alerts, 34,320 snapshots.
  - Cards: 28 planned, 27 generated by Cortex and 1 already cached, 0 failed.
- **Play at 60× from 01:45** (snaps to 01:44, one step every 2 s):
  - 01:44 Alvarez #1 HIGH 60 with her 01:34 card. Lee takes #1 at 01:56; she is back at #1 at 02:04, and Lee again at 02:10.
  - **02:14: Alvarez #1 HIGH 63**, and her card switches to the prepared 02:14 Cortex card ("Rising HR/RR, low SpO2 overnight; check Rosa now").
  - 02:18 WATCH (her 02:17 round check), 02:20 #1 WATCH 72 with the confusion-note card. This matches the prepare pass exactly.
- **Bookmarks**: jumping back from 02:34 to "02:14 Alvarez reaches #1" gives #1 HIGH 63 and clears the stale toast. "04:05 Ortiz fall" at 120× (one step per second): 04:06 Ortiz CRITICAL 90 (unassisted exit), then 04:08 CRITICAL 100 (suspected fall) and Patel CRITICAL 100 (possibly unresponsive), with alerts.
- **Resident page in replay** (`/resident/17` at 02:14): the prepared card is marked "Prepared before the replay", events and charts end at 02:14, and check/note are read-only. **Back to live** restores the live queue and wall clock at once.
- **Under the hood**:
  - Tiger: ingest 23.8 rows/s, 21.7 M vitals rows, compression 14.7× (2.17 GB → 151 MB), 9 of 12 chunks compressed. Benchmark: **7,617 ms raw vs 219 ms from `vitals_1h` (34.7×)**, 20,152 resident-hours, mean HR 70.5 bpm both ways.
  - Snowflake: 5 models × 120 series, 58,062 anomaly results, 1,200 care-plan chunks for 120 residents, CARE_PLAN_SEARCH active. Calls since the counter table was created: SQL REST API 124, Cortex Search 44, inference REST 2 (both 403), connector 34.
  - Outcome on the night: **904 fixed-threshold alarms** (492 HR < 50, mostly beta-blocker residents; 199 SpO₂ < 90; 177 bed exits) for 110 residents, vs **23 Vigil alerts** for 4 residents. Kim (COPD): 18 fixed alarms, 0 Vigil alerts.
  - **Alvarez**: Vigil HIGH at 00:42, 1 h 18 min before her 02:00 round (which reached her at 02:17) and 3 h 20 min before the first fixed vital-sign alarm (RR > 24 at 04:02).

Known issues and assumptions:
- **Two Alvarez times are earlier than the bookmarks.** Her first WATCH on the night is a brief borderline one at 22:26 (score 36); the bookmark marks the sustained one at 23:12. Her first HIGH is a brief 00:42–00:50 (an SpO₂ dip); the bookmark marks the 01:34 one. Under the hood reports the first ones (22:26 WATCH, 00:42 HIGH), since those are when Vigil first flagged her.
- **"#1 at 02:14" means taking #1 back from Lee.** Lee's NEWS2 floor holds him at exactly 60 from ~01:08, so he and Alvarez swap #1 between 01:34 and 02:14. She is #1 at 01:34–01:54, 02:04–02:08, and from 02:14 on.
- **Replay time moves in 2-min steps.** The header clock runs smoothly between steps, but the queue changes once per step. Seeks snap to the grid (01:45 → 01:44, 04:05 → 04:04).
- **Prepare deletes nothing** (AGENTS.md rule 8): replay steps are upserted into `replay_snapshots`, which only `make seed` (via `reset_demo.py`) truncates. While a prepare runs, the previous summary is marked in progress, so a failed run makes replay ask for Prepare again instead of trusting half-rewritten steps. The first Phase 8 prepare (before this change) also left one night of rows in `priority_snapshots`; they are harmless and go with the next seed.
- Prepare reuses a card already cached for the same (resident, as_of): this run reused the Phase 6 02:14 Alvarez card. Tick "regenerate AI cards" to replace it.
- **Cortex wording**: a few generated headlines are firmer than our guardrails ask, e.g. "infection likely". The prompt is unchanged from Phase 6; tighten it in Phase 9 if needed.
- **Snowflake inventory waits behind detection.** While `ANOMALY_DETECTION` runs, the XSMALL warehouse queues the inventory query, so the panel shows the error and retries a minute later.
- The fixed-alarm count is conservative: readings < 5 min apart count as one alarm. A real monitor re-alarming every reading would count far more.
- Ingest by source shows the simulator only. The Polar band and iPhone appear once they stream (hardware still pending).

**Phase 7 (live hardware: Polar band + iPhone bed sensor): done in code; real-hardware test pending**

What works:
- **`WS /ws/ingest`** (`app/devices/ingest.py`) accepts these messages:
  - `hello`
  - Polar `{resident_id, ts, hr, worn}`, written as source 1
  - iPhone `{resident_id, ts, motion_energy, peak_jolt, gravity}`, written as source 2
  - `calibrate` begin/finish/cancel
  
  Only R-120 takes device data, and none while the simulated band is on.
  - A device timestamp in the future or > 10 s old is replaced by the server's time.
  - Rows are buffered and written every 1 s. A bed transition triggers an immediate write. The rows and the `bed_exit`/`bed_return` event go in one writable-CTE statement on the writer's own connection (one round trip, ~0.3 s to Tiger Cloud).
  - After a transition, the writer pokes the evaluator and broadcasts the event on `/ws/live`. Each reading is also broadcast as a `device` message, used by `/devices` and R-120's chart.
- **Bed state machine** (`app/devices/bed_state.py`, pure): calibration (empty 5 s, lie still 10 s, shift 5 s, with unrecorded prep pauses) derives the quiet-energy and burst thresholds. Three detection modes:
  - **tilt**: an under-pillow phone tips with the head's weight. Exit after 2 s of empty-bed tilt.
  - **breathing motion**: a burst, then 4 s of empty-bed quiet.
  - **SPEC fallback**: a burst, then ≥ 10 s still; another burst is a return.
  
  Rolling over is not an exit. The tracker also reports restlessness levels and `bed_motion` on the simulator's 0–10 scale. The calibration and the last bed state survive restarts.
- **Fall-prevention → AI card fast path:** the evaluator calls `Assessor.on_alert` the moment it raises an alert, so the Cortex call starts in the same second (30 s debounce).
  - While it runs, the card shows the deterministic template. For bed exits and falls, the template cites the care plan's "Fall precautions: bed alarm; assist ×1 to bathroom; …" and mobility lines (verbatim, from the same roster fields).
  - An older Cortex card yields to the template when the rule in force started after it.
  - For fall situations, the prompt asks Cortex to quote the fall-precautions line.
- **Latency fixes**:
  - The evaluator now publishes the queue *before* writing snapshots.
  - A poke that lands mid-cycle is no longer lost: the wake event is cleared before the cycle, not after.
- **UI**:
  - `/devices`: Connect Polar (Web Bluetooth, `0x2A37`; the parser handles 8/16-bit HR, contact bits, energy and RR intervals), big live HR, contact, stream counts, auto-reconnect with backoff, and a QR code for `PUBLIC_URL/bed/120` (falls back to the page's own HTTPS origin). It also shows the live bed state and calibration mode, and the device list with Tiger-writer stats.
  - `/bed/[residentId]`: Start (iOS motion permission + screen wake lock), 1-s summaries, a motion meter, the in-bed/out-of-bed state, and spoken calibration. The page has no header and plays no chime.
  - R-120's HR chart appends live band readings each second, with a "Polar live" badge.
  - Admin: **Bed exit R-120** / **Back in bed** (the existing Phase 4 injector routes R-120's bed exit to the bed-sensor path when the simulated band is off).
  - `/api/devices`, `POST /api/devices/bed/{id}/state`.
- **Tests**: backend **322** passed: the state machine on synthetic traces (tilt/energy/burst modes, rolling over, missing gravity, prep pauses, restlessness), the ingest protocol, clock clamping, COPY/null encoding, the writer's retry, the WS round trip, the alert → Cortex trigger + debounce, and that the template citation is verbatim in R-120's care plan. Frontend **41** passed: the BLE parser (8/16-bit, contact bits, RR, energy, truncation), the motion summary, calibration steps, the live-HR trail and appending it to charts. `make lint` is clean.

Verified against the running dev stack (scripted Polar + iPhone clients over `/ws/ingest`, Force night on):
- Band HR reaches `/vitals` (vitals_1m real-time cagg) in 0.3–3 s. The chart shows it in ~1 s through `/ws/live`.
- Exit timeline, measured on `/ws/live`:
  - +3.0 s after getting up: exit detected (1-s summaries + 2-s tilt confirm).
  - +3.5 s: `bed_exit` event stored.
  - **+4.2 s: R-120 #1 CRITICAL 90** (was +7.1 s before the latency fixes).
  - +15 s: Cortex card.
- Cortex card: "Out of bed alone, hip precautions, high fall risk". Checks include "Confirm bed alarm and walker are in place". It cites "Fall precautions: bed alarm; assist ×1 to bathroom; walker at bedside; non-skid socks." and "Hip precautions: must call for help to get out of bed; never up alone."
- `/bed/120` in a mobile viewport with synthetic motion events: in bed → Out of bed → back in bed. The permission-denied path shows its message.

Known issues:
- **Not yet tested with the real Polar band or iPhone** (no hardware in this session). The thresholds come from calibration, but under-mattress placement may only reach the 10-s fallback mode; under the pillow is the intended spot.
- **Tiger round trip is ~280 ms from this Mac.** The evaluator cycle averages ~1.2 s (max 3.7 s; pre-existing), and it dominates the exit → #1 latency.
- After a test exit, R-120 stays at 90 for 5 min after returning to bed (the Phase 3 rule hold) unless a check is logged.
- `PUBLIC_URL` is re-read per request in `make dev`. In Docker, the container's env is fixed at start, so restart it (on Vultr it is the fixed domain anyway).
- Polar ACC/PPI via the PMD service is requested but not used (P2).

**Phase 6 (care plans, Cortex Search, AI assessment): done**

What works:
- **Care plans** (`app/sim/care_plans.py`, `scripts/generate_care_plans.py`): one 1-page reportlab PDF per resident in `data/care_plans/resident_NNN.pdf`, built from the seeded roster, so they match Tiger. Each has 8 sections: diagnoses and history, medications and monitoring, code status/POLST, mobility and fall precautions, baseline cognition and behaviors, night routine, escalation, family contact.
  - Cast lines: Grant "bed alarm; assist ×1 to bathroom". Patel "POLST on file: comfort-focused treatment. No CPR…" and "…notify hospice + daughter Anjali. No CPR." Alvarez "FULL code", "History of UTIs presenting as confusion…", "Notify provider for acute mental status change…".
- **Snowflake docs pipeline** (`sql/snowflake/003_docs.sql`, `make care-plans`):
  - Stage `DOCS.CARE_PLANS` (directory table, SSE), then `PUT`, then `AI_PARSE_DOCUMENT` (LAYOUT) into `DOCS.CARE_PLAN_DOCS`.
  - Each document is split at its Markdown headings, then `SPLIT_TEXT_RECURSIVE_CHARACTER` (1000/150) makes `DOCS.CARE_PLAN_CHUNKS(resident_id, section, chunk)`.
  - `CORTEX SEARCH SERVICE DOCS.CARE_PLAN_SEARCH ON chunk ATTRIBUTES resident_id, section`.
  - Verified: 120 docs parsed, 1,200 chunks. The run took 406 s to parse and 178 s to build the service. Tiger `residents.care_plan_uri` is set.
- **REST client** (`app/db/snowflake_rest.py`):
  - Key-pair JWT. iss/sub = `ACCOUNT.LOGIN_NAME` upper-cased, as the connector does. On this account the user NAME is lowercase and case-sensitive, but the JWT is matched on LOGIN_NAME (the email in `SNOWFLAKE_USER`).
  - Methods: `cortex_complete`, `search_care_plan` (REST `:query`, `@eq` filter on resident_id), `sql` (SQL API `/api/v2/statements`) and `analyst_message` (stub for Phase 9).
  - Every call is counted under `rest.*` in `/api/stats/snowflake`, which also shows `assessments` stats and REST status.
- **AI assessment** (`app/deep/assess.py`):
  - Context: profile + meds/flags, the deterministic score and top factors, the NEWS2 breakdown, the last 2 Snowflake anomaly buckets (value vs expected and 99 % range), the Tiger personal baseline, an 8 h hourly trend, 12 h of events/notes/intake, the forecast (none yet) and the top 4 Cortex Search chunks. The search runs in parallel with the Tiger reads.
  - Guarded prompt: only facts in the context, no diagnosis, cite the numbers, bedside checks, respect code status/POLST, quote the care plan verbatim.
  - `validate()` coerces the shape and **drops any citation whose quote isn't in the retrieved chunks**.
  - Results are stored in Snowflake `CORE.AI_ASSESSMENTS` (via the SQL API) and Tiger `ai_insights` (kind `assessment`), plus a `/ws/live` `insight` message.
  - Triggers (live, scanned every 10 s): tier rises to HIGH/CRITICAL (debounced 2 min), or a top-5 non-STABLE resident whose assessment is > 15 min old (at most one try per 15 min). Also the Refresh button (`POST /api/explain/{id}[?as_of=]`). One call in flight per resident, 2 at a time.
  - The card shows a Cortex assessment ≤ 60 min old and known by `as_of`, else the Phase 4 template (same JSON shape). Callers wait at most 8 s. A slower call keeps running (`pending: true`) and replaces the template when it lands. An error falls back at once.
- **UI**:
  - AI card: headline, assessment, possible concerns, recommended-checks checklist, escalation, expandable care-plan citations, "Snowflake Cortex · model · HH:MM", and Refresh. Refresh shows "Asking Snowflake Cortex…" until the WS `insight` arrives.
  - Queue cards show the Cortex headline with its time ("AI summary pending" for HIGH/CRITICAL residents without one).
- **Tests**: backend **287** passed (JWT claims/signature, response parsing, the SQL-API fallback, the search request, care-plan sections and cast lines, PDF render, citation validation, query wording, trigger/debounce, headline age, timeout/error fallback). Frontend **22** passed. `make lint` is clean.

Verified against the real accounts (as_of = last night via `POST /api/explain/{id}?as_of=`):
- **Alvarez at 02:14** (HIGH 63), `claude-sonnet-5`, via the SQL API:
  - Headline "Rising HR/RR, low SpO2 overnight; check Rosa now".
  - Concerns: "Possible early infection (UTI) per history" and low fluids.
  - Checks: mental status vs baseline, temperature now, fluids and urine, breathing/SpO2.
  - Cites "History of UTIs presenting as confusion…" and "Notify provider for acute mental status change…".
  - Caveat "No documented confusion or fever yet". It invents no confusion: the 02:20 note is not in its context.
- **Patel at 04:20** (CRITICAL, unresponsive rule):
  - Checks: responsiveness, breathing comfort, pain, positioning/mouth care.
  - Escalation: comfort check, then hospice + daughter Anjali, no CPR, no transfer.
  - Cites the POLST line and his escalation line.
- **Snowflake unreachable** (second backend with HTTPS routed to a dead proxy): `POST /api/explain/101` returns the template with "Snowflake Cortex unavailable (ConnectError); showing the template". Patel's comfort-care escalation is still there, and the resident page returns 200.
- **Live**: the dev backend assessed the top residents automatically, and the queue shows their headlines. `CORE.AI_ASSESSMENTS` and Tiger hold the same 11 rows.

Known issues:
- **REST `inference:complete` is refused for this account** (403 `003001` "This account is not allowed to access this endpoint", for every model, with valid JWT auth). The client tries it, then runs `SNOWFLAKE.CORTEX.COMPLETE` through the **SQL REST API** with the same messages, and retries the endpoint every 30 min. If you can get the endpoint enabled (Snowflake support / non-trial account), nothing needs to change. Cortex Search REST works.
- **Latency**: a Cortex completion takes ~10–15 s, plus ~1.5 s for search and ~1–2 s for context. So a Refresh usually passes the 8 s budget, shows "Asking Snowflake Cortex…", and gets the new card ~15 s later. Automatic triggers pre-compute cards for flagged residents.
  - COMPLETE's `response_format` (structured output) made the same call take > 45 s, so on the SQL-API path the JSON schema goes in the prompt and the output is validated.
  - `temperature` is not sent: `claude-sonnet-5-5` / `opus-5-5` / `haiku-5-5` reject it ("malformed request").
  - `CORTEX_MODEL` stays `claude-sonnet-5` (passes `make check`, ~9–13 s). `sonnet-5-5` ignored the length limits in one run and hit the token cap.
- **Personal baseline in the context** comes from today's `resident_baselines`, even for a historical as_of (the hourly job keeps no history).
- Care-plan title blocks are chunked under their own heading ("Care Plan: <name>"), so `CARE_PLAN_CHUNKS` has 129 distinct section names. Harmless: those chunks rank low.
- `make care-plans` re-parses all 120 PDFs (~7 min) on every run.

**Phase 5 (Snowflake core: sync + anomaly detection): done**

What works:
- **Snowflake tables** (`backend/sql/snowflake/002_tables.sql`, `make migrate-sf`, idempotent).
  - `VIGIL.CORE`: `RESIDENTS, STAFF, VITALS_15M, EVENTS, CHECKS, NURSE_NOTES, ALERTS, ANOMALY_RESULTS, FORECAST_RESULTS, AI_ASSESSMENTS, HANDOFFS`.
  - Timestamps are `TIMESTAMP_NTZ` holding UTC. JSON lands as real `VARIANT`/`ARRAY`: `write_df` stages through a temp table and `PARSE_JSON`.
  - `001_setup.sql` stays the one-time ACCOUNTADMIN bootstrap (`--include-setup` runs it too).
- **Sync** (`app/deep/sync.py`):
  - `make seed` ends with a bulk load: 120 residents, 15 staff, ~37k `vitals_15m` buckets and ~10k events, split into `EVENTS` + `CHECKS` / `NURSE_NOTES` / `ALERTS`. It takes ~25 s on a 3-day seed.
  - The backend then syncs every 60 s: closed buckets past the watermark plus new events, via `write_pandas`. Watermarks and cumulative row counts live in Tiger `sync_state`.
  - A bucket is synced only once it ended 5 min before the simulator's data frontier, so cagg refreshes are final and a catch-up backfill is never synced half-written.
  - A Tiger advisory lock keeps the seed's bulk load, `make train`/`make detect` and the backend loop from overlapping. After a restart or error the first cycle deletes Snowflake rows past the watermarks (no duplicates).
  - Snowflake truncation lives in `scripts/reset_demo.py` (AGENTS rule 8).
- **Anomaly models** (`sql/snowflake/004_models.sql`, `make train`):
  - 5 multi-series `SNOWFLAKE.ML.ANOMALY_DETECTION` models: `ML.AD_HR` (HR_AVG), `AD_RR` (RR_AVG), `AD_SPO2` (SPO2_MIN), `AD_SKIN_TEMP` (SKIN_TEMP_AVG), `AD_MOTION` (MOTION_SUM).
  - Each has `SERIES_COLNAME = RESIDENT_ID`, `TIMESTAMP_COLNAME = BUCKET_TS`, exogenous `IN_BED_FRAC`, and is unsupervised.
  - **Movement is night-time restlessness in bed only** (in bed ≥ 90 % of the bucket, 23:00–06:59 facility time), in training and in detection (`anomaly.ROW_FILTERS`). Over a whole day a resident's movement runs from ~40 counts / 15 min asleep to thousands walking. Each model has one error width per resident, so the all-day night band was about ±1,600 counts and no in-bed restlessness could fall outside it. This narrows SPEC §9 ② (which only names the target and `IN_BED_FRAC`).
  - The training window lives in `ML.TRAINING_WINDOW`: up to `ANOMALY_TRAIN_DAYS` (default 12) days ending at last night's holdout start, clamped to the seeded history. The five train in parallel and each one's time is printed. Optional `SNOWFLAKE_TRAIN_WAREHOUSE` trains on a bigger warehouse.
  - Training metadata goes to Tiger `sim_meta.anomaly_models`, tied to the seed. A re-seed invalidates the models until `make train` runs again.
- **Detection** (`app/deep/anomaly.py`):
  - `DETECT_ANOMALIES` with `prediction_interval 0.99` for all residents, the five models in parallel. It runs after each sync that closed new buckets, and on demand via `make detect`.
  - Severity: z from `PERCENTILE` (`DISTANCE` once it saturates). 0 inside the 99 % interval, linear to 1 at |z| = 5, one-sided for SpO₂ (low) and movement (high).
  - Every call gets a 24 h lead-in of preceding buckets (never before the end of the training data), and lead-in results are dropped. The model conditions on the input's own recent buckets, so a window that starts cold flags almost everyone in its first buckets. With the lead-in, a night-only run matches a contiguous run exactly.
  - Results go to `CORE.ANOMALY_RESULTS` and are mirrored to Tiger `ai_insights` (kind `anomaly`), stamped bucket end + 10 min (when live knows them, so replay never sees them early). Re-detection replaces earlier results for those buckets.
- **P_anom** (`priority.py`):
  - When a resident has a Snowflake result from the last 45 min, P_anom = 1 − Π(1 − severity) over the worse of the last 2 buckets per metric. "Anomalous" (multi-signal) means outside the 99 % interval. Otherwise the Tiger z fallback applies.
  - Factor chips read e.g. "HR 86 (her expected 56–66)" and "Skin temp +0.8 °C vs her expected".
  - "Why this score" shows the source and when the result became known.
- **Charts**:
  - HR, RR, SpO₂ and skin temp get the model's expected range (dashed edges, light fill). Every flagged bucket gets an amber ◆ on the line: HR, RR, SpO₂, skin temp and movement.
  - Data comes from `GET /api/insights/{id}?kind=anomaly&from&to`, read from the Tiger cache; the charts never wait on Snowflake.
- **`GET /api/stats/snowflake`**: calls / errors / avg ms per feature (`connect`, `sync.*`, `bulk.*`, `anomaly.detect.<metric>`, `anomaly.store`, `anomaly.train.*`, …), the sync and detection state, watermarks, cumulative rows, the model metadata and ai_insights counts. `/api/health` adds `deep`.
- **Tests**: backend **253** passed (severity mapping, PERCENTILE/DISTANCE z, detection lead-in, the night in-bed motion filter, result parsing and payloads, the SQL, P_anom from Snowflake vs the fallback, multi-signal, factor text, sync frames and cutoffs). Frontend **21** passed (expected band at 1m/1h and the dots). `make lint` is clean.

Alvarez scenario (SPEC §8, updated):
- She never gets up alone. Restlessness in bed grows from ~4–5× her normal at 22:00 to ~6–7× by 02:00. She makes 2 call-light toileting trips (22:50, 03:10), each with a CNA check 2 min before, so the fall-prevention rule never fires.
- Her early-evening drift is a little stronger than in the first Phase 5 version: at 23:40, HR +13, RR +3, skin temp +0.65 °C. Her 02:14 values are unchanged.
- The 21:30 note ("a bit restless at bedtime, only drank half her fluids") is low concern with no confusion wording. The keyword fallback scores a hedged note whose only findings are restlessness or reduced intake as low; unhedged wording stays med.
- Her 02:00 round reaches her late, at 02:17, so no post-check suppression sits on her climb. The 02:20 CNA note "seems more confused than usual, warm to touch" adds the confusion.
- No vital-sign fixed threshold fires before ~03:50 (first: RR > 24 at ~04:04). Max HR before 03:50 is 99 bpm.

Verified against the real accounts (10-day seed, `make seed DAYS=10`, 11 min including the Snowflake bulk load of 115k buckets):
- **`make train`** on `VIGIL_ML_WH` (MEDIUM, auto-suspend 60): window 7 Oct 17:15 → 16 Oct 19:00 UTC (9.1 days). Per model: SpO₂ 3607 s, motion 3645 s (31k rows), skin temp 3936 s, RR 4009 s, HR 4024 s, so 67.2 min in total for the five in parallel. The earlier 8.5–8.6-day runs took 67–70 min. MEDIUM barely helps, so training cost grows with history: 2 days on XSMALL took 19 min.
- **Detection** from the end of training to now (25 h, 44,055 results) took 272 s.
- **Last night** (`make detect`):
  - **Alvarez is flagged on HR and RR from 22:15 (30 and 31 buckets) and night movement from 23:00 (6 buckets)**, all before 02:14. Skin temp and SpO₂ are flagged later (02:15, 02:30).
  - Kim: 3 low-severity flags (≤ 0.23). Rossi: 1 movement flag at severity 0.09.
  - Outside the cast: 253 flags over ~3,900 resident-buckets × 5 metrics, ~1.3 %. On the 2-day models it was ~3 %.
- **Gated replay** (the real evaluator and alert gate, 2-min steps, Snowflake severities all evening):

  | Time | Alvarez | Driven by |
  |---|---|---|
  | 23:12–23:40 | WATCH 44–51, #1 | "HR 77 (her expected 56–69)", RR vs expected |
  | 00:32 | WATCH 56, multi-signal | "Restless: movement 3× her expected", RR, HR |
  | 00:42–00:50 | HIGH 61, #1 | brief: an SpO₂ dip adds NEWS2 points |
  | 01:34 | **HIGH 60, #1** | movement 2× expected, RR, overdue check |
  | 02:14 | **HIGH 63, #1** | RR 21 (her expected 17–19), HR 84 (her expected 67–79) |
  | 02:20 → 02:42 | WATCH 72 → CRITICAL 84 | the late 02:17 round check holds her at WATCH, then the 02:20 confusion note |

  - Before 02:20 her score never includes a confusion note or a bed-exit rule.
  - Earlier in the evening she is briefly WATCH twice: 21:02–21:26 (RR above expected, overdue round) and 22:26–22:48 (36, borderline).
  - Kim and Rossi stay STABLE all night. Lee is HIGH from ~00:34 but below Alvarez at 02:14.
- **Resident page** (`/resident/17`, 24 h): ◆ dots on HR, RR, SpO₂, skin temp and night-time movement. "Why this score" shows "Snowflake anomaly detection · known HH:MM".
- **Live**: buckets sync ~5 min after they close. Detection of one new bucket takes ~100–330 s, so results land ~8–10 min after the bucket.
- **Without Snowflake**: with `SNOWFLAKE_ACCOUNT`/`USER` blank, the app runs (sync `disabled`, queue/resident/insights all 200) and scores from the Tiger z fallback wherever no fresh result exists. With an unreachable account, the sync loop logs, backs off and retries; the event loop is unaffected.

Known issues:
- **Bookmark timing**: HIGH comes at 01:34, not 01:30. The score is 60 at 01:30, and persistence needs 2 cycles. She also dips to WATCH between 00:56 and 01:34, when an SpO₂ dip ends and the multi-signal +10 drops off.
- **Model forecasts follow recent buckets**, so a slow, steady drift is partly absorbed. Her movement flags are strongest 23:00–00:45; HR and RR carry the later part.
- **After her scripted recovery** (drift ends at noon), live detection can flag her HR as *low* vs expected for a while. That shows as ◆ below the band on the next day's chart. Harmless to the score.
- **Training is slow**: ~67 min for 9 days on MEDIUM. Train well before a demo. `make detect` alone takes ~5 min, and `make train --no-train` re-registers models already in Snowflake (e.g. after a lost connection) and detects without retraining.
- **Tiger connections dropped while idle**: something on the network path closes quiet connections (the server has no idle timeout). All Tiger connections now send TCP keepalives every 30 s, and `make train` holds no Tiger connection while it trains.
- A detection run happens inside the sync loop, so the 1-min sync pauses for its ~2–4 min once per bucket.
- Rows synced and call counts in `/api/stats/snowflake` are per backend process. Cumulative rows come from `sync_state`; training is done by `make train`, so its call counts appear only in that process's output and in `sim_meta`.

**Phase 4 (core UI: Rounds Queue, resident detail, admin): done**

What works:
- **`/` Rounds Queue**, live over `/ws/live` with no page refresh.
  - Header: facility, display clock (honours the force-night display offset; moon icon at night), Live/Replay badge, Paused badge, connection status, role switcher (CNA/LPN, Charge RN, DON; kept in localStorage), alert-sound toggle, staff on duty (`GET /api/staff`, wide screens).
  - KPI strip: count per tier, overdue checks, device issues.
  - Ranked cards for every non-STABLE resident: tier chip (icon + label + score), code status, rule banner, top-3 deterministic factor chips (icon per kind), 2 h HR sparkline (inline SVG), HR/RR/SpO₂, time since check (ticks each minute), in/out of bed, band off, "quiet after check", an AI-summary placeholder, **Open** and a **Mark checked** quick form (`POST /api/checks`). Cards glide to their new rank (FLIP via the Web Animations API, off under reduced motion). STABLE residents are one-line rows ("Show all").
  - Floor grid: 120 rooms by unit, tier color + tier icon, CRITICAL rooms pulse (a static outline under reduced motion), a band icon for device issues, every room links to the resident.
  - Toasts for HIGH/CRITICAL `alert` messages, with a chime for CRITICAL (Web Audio, unlocked on the first tap). HIGH toasts fade after 20 s. CRITICAL toasts stay until opened, dismissed, a check is logged for that resident, or they drop below HIGH.
- **`/resident/[id]`** (ids 1–120 prerendered, `params` read inside Suspense):
  - Header with a large code-status badge, live tier/score/rank, mobility, Morse, cognition, contact, diagnoses, flagged meds.
  - Assessment card: the deterministic template (`app/deep/assess.py`, now part of `GET /api/residents/{id}` as `assessment`). It has the same JSON shape as the Phase 6 Cortex output: headline, assessment, "consider checking" checklist, escalation by code status + care-plan profile, citations (empty until Cortex Search), Refresh.
  - ECharts stack with a shared crosshair, ranges 6 h / 24 h / 7 d from `vitals_1m` / `vitals_15m` / `vitals_1h`:
    - HR and RR with the personal p05–p95 band for each 4-hour block.
    - SpO₂ with the NEWS2 zones (scale 2 for COPD).
    - Skin temperature as change vs usual.
    - Movement bars and the in-bed strip.
    - "— not equipped" when a metric has no data.
  - "Why this score" bars for each §7d component, with the multi-signal, rule and NEWS2 floors explained.
  - NEWS2 table; Log check; Add note (`POST /api/notes`, text only, a `note_added` event that the keyword concern fallback scores); 24 h events timeline in plain language.
- **`/admin`**: pause/start, force night, "show the clock as HH:MM" (sets `display_clock_offset_s`), simulated band for R-120, the four scenario injectors with a resident picker and the active-scenario list, **Reset demo** (`POST /api/sim/reset`), and the `/ws/live` heartbeat panel.
- **Backend additions:**
  - `POST /api/notes`, `GET /api/staff`, `POST /api/sim/reset`.
  - Checks and notes are broadcast as `event` messages on `/ws/live`, so open pages refresh.
  - **Slimmer `/ws/live` queue:** `Queue.to_wire()` sends **~34 KB** per message, down from ~80 KB, or **~4 KB on the wire** with permessage-deflate (negotiated by uvicorn + browsers). It omits the profile fields, which come from `GET /api/residents` once, and the score breakdown, which comes from the resident fetch. Scores are rounded and timestamps are whole seconds. STABLE cards carry 1 factor, and sparklines go only to flagged cards plus the top 24. `GET /api/queue` is unchanged.
- **Tests:** backend **227** passed (wire format, sparkline cap, reset, staff on duty, note validation, the assessment template). Frontend vitest **17** passed (toast stack rules, queue join + floor layout, baseline bands by facility block, SpO₂ zones, display-clock offset, event text). `make lint` is clean and `pnpm build` prerenders all 126 pages.

Verified in the in-app browser against the running stack and real Tiger data:
- The queue updates live. Clicking a card opens the resident page with every chart populated, e.g. Alvarez over 24 h: HR 66 → 92 between 22:00 and 03:45 (1-min max 98; the first reading over 100 is in the 03:45 bucket), then ~95–100 until 06:30 and up to ~120 around breakfast (07:45–08:30). RR rises 16 → 25 overnight and skin temp reaches +1.2 °C.
- **Injected fall** (`/admin` → James Ortiz): about 38 s later Ortiz moved to **#1, CRITICAL 100** ("Suspected fall … then no movement"), with a toast. **Mark checked** on his card cleared it at the next cycle.
- Phone (375 px) and iPad portrait (768 px): no horizontal scroll, single column with the floor grid below the cards. Two columns from ~1090 px (iPad landscape).

Known issues and assumptions:
- **Admin reset never touches the database** (AGENTS.md rule 8). It resets the demo controls and injected scenarios; re-seed with `make seed`.
- The AI headline on queue cards is a placeholder until Phase 6. The resident page shows the deterministic template, labelled as such.
- The alert chime needs one tap or keypress on the page first (browser autoplay rules).
- SpO₂ is read every 5 min, so the 1-min chart joins readings across empty minutes. RR is shown only while in bed (gaps otherwise).
- Charts refresh every 30 s (6 h), 2 min (24 h) and 10 min (7 d). The resident detail refreshes every 15 s and right after a check or note for that resident.

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
