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
| `make migrate-sf` | Create the Snowflake `VIGIL.CORE` tables (`backend/sql/snowflake/002_tables.sql`, idempotent) |
| `make seed DAYS=3` | Truncate Tiger, regenerate N days of synthetic history ending now (N ≥ 2), then bulk-load Snowflake |
| `make sf-load` | Re-run the Snowflake bulk load alone (empties `VIGIL.CORE` first) |
| `make train` | Train the 5 Snowflake anomaly models (last night held out), detect since, summarize the cast |
| `make detect` | Re-run Snowflake anomaly detection over last night (or `--from/--to`) and summarize |
| `make test` | backend pytest + frontend vitest |
| `make lint` | ruff + eslint + `tsc --noEmit` |
| `make tunnel` | cloudflared HTTPS tunnel to :8080 |

## Layout

The layout follows SPEC §5. `backend/app/` holds `api/`, `ws/`, `db/` (`tiger.py`, `snowflake_sql.py`, `snowflake_rest.py`), `sim/`, `scoring/`, `deep/` and `devices/`. SQL lives in `backend/sql/{tiger,snowflake}` and scripts in `backend/scripts/`. `frontend/` is a Next.js App Router app. Modules for later phases are placeholders whose docstrings name the phase that fills them in.

## Status

**Phase 5 (Snowflake core: sync + anomaly detection): built; acceptance partly met (see Known issues)**

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
- **Tests**: backend **245** passed (severity mapping, PERCENTILE/DISTANCE z, detection lead-in, result parsing and payloads, the SQL, P_anom from Snowflake vs the fallback, multi-signal, factor text, sync frames and cutoffs). Frontend **21** passed (expected band at 1m/1h and the dots). `make lint` is clean.

Verified against the real accounts (3-day seed):
- **`make train`**: window 13 Oct 19:30 → 15 Oct 19:00 UTC (2.0 days). Training took 1081–1151 s per model on XSMALL, all five in parallel, 19 min in total. Detection over the following 30 h (62,332 results) took 237 s.
- **Last night** (`make detect`):
  - Alvarez is flagged on **HR from 23:30, RR from 22:00, skin temp from 23:00**, all before 02:14. **Motion is not flagged** (see below).
  - Rossi: one SpO₂ flag (a real simulated dip, 92 % vs 95 % expected). Kim: 11 low-severity flags (HR below expected, skin temp).
  - In the gated replay, Kim briefly reaches WATCH twice (36–40) and Rossi once (38). Neither reaches HIGH.
  - Every resident outside the cast: ~3 % of resident-buckets flagged, against 1 % expected for a 99 % interval.
- **Live**: buckets sync ~5 min after they close. Detection of one new bucket takes ~100–230 s, so results land ~8–10 min after the bucket.
- **Without Snowflake**: with `SNOWFLAKE_ACCOUNT`/`USER` blank, the app runs (sync `disabled`, queue/resident/insights all 200) and scores from the Tiger z fallback wherever no fresh result exists. With an unreachable account, the sync loop logs, backs off and retries; the event loop is unaffected.
- **Alvarez HR** last night: max 98 bpm before 03:50; the first reading > 100 is at 03:51:40 (SPEC §8 holds).

Known issues:
- **2 days of training is too little.** The models don't learn the time-of-day pattern from two nights. Alvarez's in-bed night movement is forecast at ~700–1400 counts with an interval of −900…2500, while her real nights have a median of 37, so her 3× restlessness is invisible. Kim's night HR is expected near her daytime HR, which causes the false flags. Re-seed with `make seed DAYS=14` (12-day window) and retrain.
- **Training time** scales with history. 2 days took 19 min on XSMALL, so 12 days may take well over an hour. Use `SNOWFLAKE_TRAIN_WAREHOUSE` (MEDIUM or larger) for `make train`.
- **The Alvarez bookmarks (WATCH 23:40 → HIGH 01:30 → #1 02:14) do not hold, with or without Snowflake.** Two SPEC floors fire first:
  - The 21:30 "more confused than usual" note makes NEWS2 consciousness 3, which forces HIGH from 21:32.
  - Each of her 4 unassisted night bed exits (Morse 55, 1-assist) is fall prevention = 90, i.e. CRITICAL at 22:38, 23:31, 01:52 and 03:51.

  Snowflake only moves the ≤ 25-point anomaly term. Results: 23:40 CRITICAL (bed exit held), 01:30 CRITICAL 81, 02:14 HIGH 79 at #1. Her brief WATCH dips are the 30-min suppression after logged checks. A scenario or spec change is needed (not weights); this is open with the human.
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
