# Vigil — Product & Technical Spec (source of truth)

> Working name: **Vigil** (rename later with a find-and-replace). This file is the single source of truth for every coding agent working on this repo. If code and spec disagree, the spec wins unless the human says otherwise.

## 0. Hackathon context (why the design looks like this)
- Built for Cal Hacks 13.0, solo, 36 hours. Target prizes: **[MLH] Best Use of Snowflake API** and **[MLH] Best Use of Tiger Data**, plus Best Solo Hack and People-First Social Impact.
- Each sponsor must do work that only it can do, and the demo must *prove* it on screen:
  - **Tiger Data = live operational layer** ("what is happening now and how it is trending"). High-rate ingest, hypertables, continuous aggregates, compression, hyperfunctions, scheduled jobs.
  - **Snowflake = intelligence layer** ("what it means, what happens next, what to do"). ML anomaly detection, forecasting, Cortex AI functions, Cortex Search, Cortex Analyst, Snowflake REST APIs.
- **Never use Tiger's AI features** (pgai, pgvector, vectorizers). All AI lives in Snowflake so neither sponsor looks bolted on.
- All data is synthetic. No real PHI. No cameras anywhere.

## 1. Product
**One-liner:** Vigil watches every resident of a nursing home around the clock, learns each resident's personal "normal," and gives the night shift a live, ranked list of **who to check next and why**.

**Problem:** Understaffed night shifts (1 aide : 30+ residents) do fixed rounds every 2 hours. Residents decline between rounds. In the elderly, deterioration is often silent (UTI/sepsis/delirium shows up as restlessness and a modest HR rise, not fever), so fixed alarm thresholds miss it, while also causing alarm fatigue.

**Framing (use in all UI copy):** decision support that catches decline earlier. Never claim it "prevents deaths," "diagnoses," or "replaces staff."

**Users / roles** (no login; a role switcher in the header):
- **CNA / LPN (night shift)** — Rounds Queue on a tablet or phone.
- **Charge Nurse (RN)** — escalation, resident detail, shift handoff.
- **Director of Nursing** — facility analytics ("Ask the facility").
- **Family** (P2 stretch) — gentle status view.

**Two-speed architecture (core story):**
- **Fast tier — Tiger Data, seconds:** emergencies and hard rules (falls, extreme vitals, unresponsive, high-fall-risk bed exit).
- **Deep tier — Snowflake, minutes:** slow silent drift, judged against each resident's own learned normal, explained with care-plan context.

## 2. The simulated facility
- **Sunrise Gardens Skilled Nursing Facility**, 120 residents, 3 units × 40: **East** (long-term care, rooms 101–140), **West** (post-acute/rehab, rooms 201–240), **Memory Care** (dementia, rooms 301–340). Resident IDs are 1–120.
- Night staff: 1 RN, 1 LPN, 3 CNAs (seed a `staff` table with names/roles). Scheduled night rounds at 22:00, 00:00, 02:00, 04:00, 06:00.
- Shifts: Day 07:00–15:00, Evening 15:00–23:00, Night 23:00–07:00 (facility local time; use `America/Los_Angeles`).
- Resident attributes: name, age, sex, room, unit, diagnoses[], medications[] (with flags `beta_blocker`, `afib`, `copd`, `insulin`, `opioid`), Morse Fall Scale score, mobility (`independent` | `1-assist` | `2-assist` | `bedbound`), cognition (`intact` | `mild` | `moderate` | `severe`), code status (`FULL` | `DNR` | `DNR-DNI` | `COMFORT`), emergency contact, `is_live` (true only for R-120), and a synthetic care plan PDF.

## 3. Devices and metrics
Two devices per resident: a **wristband** (modeled on Polar Verity Sense) and an **under-mattress bed sensor** (modeled on contactless bed sensors that report presence, breathing, movement).

| Metric | Column | Device | Sim rate | Live demo source (R-120) | Used for |
|---|---|---|---|---|---|
| Heart rate (bpm) | `hr` | wristband | 5 s | **Polar Verity Sense, 1 s, live** | rules, NEWS2, baseline, anomaly |
| HRV RMSSD (ms) | `hrv_rmssd` | wristband | 5 min | not equipped (P2: Polar PPI) | anomaly feature |
| Wrist skin temp (°C) | `skin_temp` | wristband | 1 min | not equipped | anomaly feature as change vs baseline. **Not** NEWS2 (skin ≠ core) |
| SpO₂ (%) | `spo2` | wristband | 5 min | not equipped | rules, NEWS2 |
| Motion count | `motion` | wristband accel | 5 s | not equipped (P2: Polar ACC) | immobility, restlessness |
| Impact peak (g) | `impact` | wristband accel | event | not equipped | fall detection |
| Worn | `worn` | wristband | 5 s | live (Polar sensor-contact bit) | device liveness |
| Respiratory rate | `rr` | bed sensor | 5 s while in bed | not equipped | rules, NEWS2 |
| In bed | `in_bed` | bed sensor | 5 s | **iPhone under mattress (stand-in)** | bed exits |
| Bed movement | `bed_motion` | bed sensor | 5 s | **iPhone accelerometer** | restlessness, turning |
| BP, core temp | event payload | nurse spot check | 2×/day | simulated | NEWS2 |
| New confusion | from note extraction | nurse note | manual | typed/voice note → Snowflake | NEWS2 consciousness = 3 |
| Fluid/meal intake, toileting | event payload | CNA log | per meal | simulated | AI context, score |
| Check logged | event | staff action | manual | "Mark checked" button | time since check, suppression |

Missing metrics are normal (mixed equipment). Scoring must degrade gracefully and the UI shows "— not equipped".

## 4. Time model (important)
- **All stored timestamps are real wall-clock UTC. Never write future timestamps.** Tiger refresh/compression/retention policies are relative to `now()` and must keep working.
- **Every scoring/query function takes an `as_of` timestamp** instead of calling `now()`.
- **Live mode:** `as_of = now()`. Simulated residents stream in real time; the Polar band and iPhone stream live.
- **Night mode for live demo:** a UI/display offset so the *displayed* clock and night-time rules (22:00–06:00) can be forced to "night" during a daytime demo. Implement as a `display_clock_offset` + `force_night` flag in sim state, used only by night-window checks and the header clock; stored data stays real time.
- **Replay mode:** the seed writes a scripted "last night" (22:00–06:30 local, the night before the seed run) into history. Replay moves a cursor through it at adjustable speed (30×/60×/120×) and evaluates the whole pipeline `as_of` the cursor. UI banner: *"Replaying last night's recorded data through the live pipeline."*
- **Prepare replay:** precomputes Snowflake deep-tier results (anomaly flags per 15-min bucket, forecasts and AI assessments at key moments) so replay never waits on Snowflake. Results are revealed only when `ts <= cursor`.
- **Replay grid:** every evaluation is 2 min of replay time (30× → one every 4 s, 60× → 2 s, 120× → 1 s), on a fixed grid from 21:00 (a 1-h warm-up before 22:00). Prepare scores the whole grid through the real evaluator and alert gate and upserts each step into `replay_snapshots`; a seek snaps to the grid and seeds the replay's gate from those snapshots. Replay never calls Snowflake: anomaly results and AI cards come from the prepare step's cache.
- **Bookmarks** (times from the gated replay, with P_trend): 23:12 Alvarez first deviation (WATCH, #1) · 01:34 Alvarez reaches HIGH · **02:14 Alvarez at #1** (HIGH 63, #1 since 01:12; Lee WATCH 53 at #2) · 02:20 Alvarez confusion noted · 02:30 Lee forecast warning (RR forecast to cross 25 by ~04:15; his RR reaches 25 ~03:00) · 03:20 Patel immobility · 04:05 Ortiz fall.

## 5. Architecture & stack
```
Simulator ──────────────┐
Polar band → Mac Chrome (Web Bluetooth) ──┤  WS /ws/ingest
iPhone /bed/120 (DeviceMotion) ───────────┘
            ↓
FastAPI backend ──► Tiger Cloud: vitals/events hypertables, continuous aggregates, baselines
   │  fast-tier evaluator (every 2 s) → priority queue → WS /ws/live → Next.js UI
   │  sync job (every 1 min): Tiger vitals_15m + events → Snowflake
   └► Snowflake: ANOMALY_DETECTION, FORECAST, AI_COMPLETE, Cortex Search,
      AI_TRANSCRIBE / AI_EXTRACT, AI_AGG, Cortex Analyst → results cached back in Tiger ai_insights
```
- **Frontend:** Next.js (App Router) + TypeScript + Tailwind + shadcn/ui, ECharts (`echarts-for-react`), Zustand, native WebSocket. Package manager: pnpm.
- **Backend:** Python 3.12, FastAPI, `psycopg[binary,pool]` (async) for Tiger, `snowflake-connector-python[pandas]` for SQL, `httpx` for Snowflake REST, numpy for simulation, asyncio background tasks. Package manager: `uv`. Tests: pytest.
- **Single origin:** Caddy reverse proxy in front: `/api/*` and `/ws/*` → backend:8000, everything else → frontend:3000. Same Caddyfile locally and on Vultr. One origin avoids CORS and lets one HTTPS tunnel serve the iPhone.
- **HTTPS for iPhone in dev:** `cloudflared tunnel --url http://localhost:8080` (Caddy's local port). iPhone motion sensors and mic need HTTPS.
- **Hosting:** Docker Compose on a Vultr VM, Caddy auto-HTTPS, custom domain.

### Repo layout
```
vigil/
  SPEC.md  AGENTS.md  CLAUDE.md  README.md  .env.example  Makefile
  docker-compose.yml  Caddyfile
  backend/
    pyproject.toml
    app/
      main.py  config.py  state.py
      db/tiger.py  db/snowflake_sql.py  db/snowflake_rest.py
      sim/physiology.py  sim/cast.py  sim/live_loop.py  sim/replay.py
      scoring/news2.py  scoring/rules.py  scoring/priority.py  scoring/baseline.py  scoring/evaluator.py
      deep/sync.py  deep/anomaly.py  deep/forecast.py  deep/assess.py  deep/notes.py  deep/handoff.py  deep/analyst.py
      devices/ingest.py  devices/bed_state.py
      api/*.py   ws/hub.py
    sql/tiger/001_schema.sql 002_caggs.sql 003_policies.sql 004_jobs.sql
    sql/snowflake/001_setup.sql 002_tables.sql 003_docs.sql 004_models.sql 005_semantic.yaml
    scripts/check_connections.py seed.py generate_care_plans.py train_models.py prepare_replay.py reset_demo.py
    tests/
  frontend/  (Next.js app)
  data/care_plans/  (generated PDFs, gitignored)
```

## 6. Tiger Data design
**Tables**
- `residents`, `staff`, `devices` — regular tables.
- `vitals` — **hypertable**, wide: `ts timestamptz, resident_id int, hr real, hrv_rmssd real, skin_temp real, spo2 real, motion real, impact real, worn bool, rr real, in_bed bool, bed_motion real, source smallint` (0=sim, 1=polar, 2=phone). 1-day chunks. Columnstore/compression `segmentby resident_id`, `orderby ts DESC`. Index `(resident_id, ts DESC)`.
- `events` — **hypertable**: `ts, resident_id, type, staff_id null, payload jsonb`. Types: `bed_exit, bed_return, fall_suspected, check_logged, note_added, intake_logged, spot_check, device_offline, device_online, alert_raised, alert_acknowledged, alert_resolved`.
- `priority_snapshots` — **hypertable**: `ts, resident_id, score, tier, components jsonb, factors jsonb`. Written each evaluation for live. Replay steps go to `replay_snapshots` (regular table, upserted by prepare, keyed by resident + step).
- `resident_baselines` — regular: `resident_id, tod_block (0–5 = 4-hour blocks), metric, p05, p50, p95, mean, stddev, updated_at`, plus per-resident `night_bed_exits_p95`, `still_minutes_p95`.
- `ai_insights` — regular cache of Snowflake outputs: `resident_id, kind (anomaly|forecast|assessment|note_extract), as_of, payload jsonb`.
- `sync_state` — watermarks for Snowflake sync.

**Continuous aggregates (hierarchical)**
- `vitals_1m`: per resident per minute — avg/min/max hr, avg rr, min spo2, avg skin_temp, sum motion, max impact, avg(in_bed::int) as in_bed_frac, count. **Real-time aggregation ON** (`materialized_only = false`).
- `vitals_15m` on top of `vitals_1m`: same fields + `percentile_agg` for hr. **This feeds Snowflake.**
- `vitals_1h` on top of `vitals_15m`: for 7-day charts and baselines.
- P2: unit-level aggregate joined to `residents`.

**Policies:** refresh policies on all caggs; compress raw `vitals` older than 1 day; retain raw 90 days, caggs 2 years. After seeding, compress all eligible chunks so the ratio is real.

**Hyperfunctions to use (visible depth):** `time_bucket`, `time_bucket_gapfill` + `locf`/`interpolate` for charts, `percentile_agg`/`approx_percentile`/`rollup` for baselines, `stats_agg` for rolling stats, `heartbeat_agg` for device liveness, `state_agg` for in-bed/out-of-bed durations and longest still period, `add_job` for the hourly baseline job. Stats page uses `approximate_row_count`, `hypertable_compression_stats` (or columnstore stats), `timescaledb_information.chunks`, `timescaledb_information.job_stats`. If a toolkit hyperfunction is unavailable on the plan, fall back to plain SQL and note it in README.

**Baseline job (hourly, `add_job`):** per resident × 4-hour time-of-day block over trailing 7 days excluding the last 6 hours: p05/p50/p95/mean/stddev for hr, rr, spo2, skin_temp, motion; plus nightly bed-exit count p95 and longest-still-period p95.

## 7. Detection & scoring
### 7a. Fast tier (Tiger, every 2 s, as of `as_of`)
Critical rules → score 100, toast + sound:
1. HR < 40 or > 130 sustained ≥ 60 s while worn.
2. SpO₂ ≤ 88 (≤ 84 if COPD) on 2 consecutive readings.
3. RR ≤ 8 or ≥ 28 sustained ≥ 2 min while in bed.
4. **Possibly unresponsive:** worn AND no motion for > 2× the resident's `still_minutes_p95` AND (HR < their p05 − 10 OR HR signal lost while worn).
5. **Suspected fall:** impact spike, then ≥ 30 s no motion, not in bed.

**Fall prevention → score 90:** bed exit during the night window (22:00–06:00 facility time, or `force_night`) by a resident with mobility ≠ `independent` or Morse ≥ 45, unless a check was logged in the last 5 min.

**Device issue → score 30, separate icon, never an alarm:** `heartbeat_agg` gap > 5 min or `worn = false` > 10 min.

Continuous features: 15-min averages vs personal baseline as z = (value − p50) / ((p95 − p05) / 3.29); bed exits tonight; minutes since last motion; minutes since last check.

### 7b. NEWS2 subset (standard RCP chart, computed with available inputs)
| Parameter | 3 | 2 | 1 | 0 | 1 | 2 | 3 |
|---|---|---|---|---|---|---|---|
| RR | ≤8 | | 9–11 | 12–20 | | 21–24 | ≥25 |
| SpO₂ scale 1 | ≤91 | 92–93 | 94–95 | ≥96 | | | |
| HR | ≤40 | | 41–50 | 51–90 | 91–110 | 111–130 | ≥131 |
| SBP (spot check) | ≤90 | 91–100 | 101–110 | 111–219 | | | ≥220 |
| Core temp (spot check) | ≤35.0 | | 35.1–36.0 | 36.1–38.0 | 38.1–39.0 | ≥39.1 | |
| Consciousness | new confusion = 3 | | | alert = 0 | | | |

COPD residents use **SpO₂ Scale 2** (target 88–92 scores 0; on room air ≥93 scores 0; 86–87 → 1, 84–85 → 2, ≤83 → 3). Any single parameter = 3 or total ≥ 5 forces at least HIGH. Use latest spot checks within 12 h; latest note extraction within 12 h.

### 7c. Deep tier (Snowflake, per closed 15-min bucket) — see §9.

### 7d. Priority score (0–100)
```
P_news    = min(NEWS2_partial / 7, 1)
P_anom    = 1 − Π(1 − severity_m) over metrics, last 2 buckets   (Snowflake; fallback Tiger min(|z|/4, 1))
P_trend   = 1 if forecast crosses a NEWS2 3-point threshold within 2 h, 0.5 within 4 h, else 0
P_behav   = max(night bed exits / max(p95,1) capped 1, restlessness vs baseline, still minutes / (2·p95) capped 1)
P_concern = latest note extraction: none 0, low .3, med .6, high 1; new/worse confusion → 1
P_overdue = min(minutes_since_check / 120, 1)

score = 100 × (0.30·P_news + 0.25·P_anom + 0.15·P_trend + 0.10·P_behav + 0.12·P_concern + 0.08·P_overdue)
+10 if ≥3 metrics anomalous together (multi-signal pattern), cap 100
Critical rule → 100. Fall prevention → 90. Device issue only → 30.
```
Tiers: **CRITICAL** ≥ 80 or rule (go now) · **HIGH** 60–79 (within 15 min) · **WATCH** 35–59 (first on next round) · **STABLE** < 35. Each tier has an icon + color (never color alone).

Every card shows **top 3 contributing factors computed deterministically** from components (never AI-invented), e.g. "HR +22% vs her night normal", "5 bed exits (usual ≤2)", "New confusion (note 21:30)".

Alarm-fatigue controls: hysteresis (enter HIGH at 60, exit at 50); persistence (2 consecutive evaluations, except critical rules); suppression 30 min after a logged check unless score rises ≥ 10. Code status changes the recommended action, never the score.

## 8. Simulation engine
**Normal physiology (stable per-resident random params, seeded RNG):**
- HR baseline 58–88 (beta-blocker 52–64; AFib mean ~95, high variance); night −8 to −12%; activity up to +25 bpm proportional to motion; AR(1) noise.
- RR 14–18, night −1. SpO₂ 94–98 (COPD 88–92), occasional brief dips. Skin temp 33.0–34.5 °C, +0.5 during sleep. HRV 15–40 ms, higher at night.
- Motion: daytime activity peaks around meals (08:00, 12:00, 17:00); night low with turns every 30–90 min (personal); 0–3 bathroom trips per night (personal), 3–8 min each.
- Bed: in bed 20:30–06:30 ± personal offset; afternoon nap.
- Wear: band removed ~30 min daily (charging/shower).
- Staff events: CNA checks near each round time (±20 min), meal/fluid intake logs, BP + core temp spot checks 2×/day.

**Volume:** every 5 s per resident ≈ 2.07M rows/day. `SEED_DAYS` env (14 for demo ≈ 29M rows, 3 for dev). Seed with Postgres `COPY`.

**Scripted cast for "last night":**
| ID | Resident | Profile | Script | Demonstrates |
|---|---|---|---|---|
| 17 | Rosa Alvarez, 87 | Memory Care, moderate dementia, **FULL**, Morse 55 | From previous afternoon: slow UTI→early-sepsis drift. HR 68→88, RR 16→22, skin temp +0.8 °C. She never gets up alone: restlessness in bed (more frequent turning and fidgeting, wrist + bed movement ~4–5× her normal at 22:00 rising to ~6–7× by 02:00) and 2 call-light toileting trips (~22:50, ~03:10) with a CNA check logged 2 min before each, so the fall-prevention rule does not fire. 21:30 CNA note (low concern, no confusion): "a bit restless at bedtime, only drank half her fluids." Her 02:00 round reaches her late, at 02:17 (no check between ~23:40 and 02:17, so no post-check suppression before 02:14); in the demo story the aide goes because she is #1. 02:20 CNA note: "seems more confused than usual, warm to touch" (confusion discovered because Vigil sent someone). No vital-sign fixed threshold (HR > 100, RR > 24, SpO₂ < 90) fires until ~03:50 (first: RR > 24 at ~03:51). Her two assisted toileting exits do count as §13 "any bed exit" fixed alarms (alarm noise, not a sign of her decline). | WATCH 23:12 → HIGH 01:34 → **#1 at 02:14**, driven by Snowflake anomalies (HR, RR, skin temp, night motion) vs her own normal |
| 42 | Henry Lee, 91 | CHF, **metoprolol**, DNR (transfer OK) | Fluid overload: RR 17→26, slow until 01:45, a visible step to ~22 by 02:15, crossing 25 around 03:00; SpO₂ 95→90 by ~03:15; HR only +6 (masked). RN assessment 03:20, O2 + head of bed raised at 03:25 → partial improvement. Stays below Alvarez at 02:14. | forecast crossing (warning ~02:30, before RR reaches 25); AI notes beta-blocker masking |
| 101 | Arthur Patel, 94 | Hospice, **COMFORT / POLST** | 03:00–04:30 no turning far beyond pattern, HR 64→46, RR 12→8 | unresponsive rule; care plan → comfort check, notify hospice/family, no CPR |
| 110 | James Ortiz, 82 | Morse 75, 1-assist | 04:05 bathroom trip: impact spike → still on floor | fall → CRITICAL in seconds |
| 88 | Margaret Kim, 79 | COPD, SpO₂ 88–91 | normal | Scale 2 → no false alarms |
| 91 | Elena Rossi, 84 | AFib, HR 85–118 | normal | personal baseline → STABLE |
| 33 | Doris Wright, 80 | — | band removed 01:00–05:00 | device task, not alarm |
| 120 | Thomas Grant, 78 | **LIVE (presenter).** Hip fracture hx, Morse 70, 1-assist, FULL. Care plan: "bed alarm, assist to bathroom" | live | Polar + iPhone |

**Scenario injectors (admin + hardware fallback):** inject fall (any resident), start sepsis drift, remove band, bed exit, simulated band for R-120.

## 9. Snowflake design
**Objects:** database `VIGIL`; schemas `CORE`, `ML`, `DOCS`; warehouse `VIGIL_WH` (XSMALL, auto-suspend 60 s). Enable cross-region Cortex inference if the region lacks the chosen Claude model (`ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION'`).
- CORE tables: `RESIDENTS, STAFF, VITALS_15M, EVENTS, CHECKS, NURSE_NOTES, ALERTS, ANOMALY_RESULTS, FORECAST_RESULTS, AI_ASSESSMENTS, HANDOFFS`.
- Stages: `@DOCS.CARE_PLANS` (PDFs, directory enabled, server-side encryption), `@CORE.NOTE_AUDIO`, `@CORE.SEMANTIC`.
- **Auth:** key-pair (RSA) for both the Python connector and REST (JWT). PAT is an alternative but may require a network policy.

**① Sync Tiger → Snowflake** every minute: closed `vitals_15m` buckets newer than watermark + new events/checks/alerts/notes, via `write_pandas`; update `sync_state`. Seed path bulk-loads 14 days once.

**② `SNOWFLAKE.ML.ANOMALY_DETECTION` (centerpiece):** 5 multi-series models (`ML.AD_HR, AD_RR, AD_SPO2, AD_SKIN_TEMP, AD_MOTION`) with `SERIES_COLNAME = RESIDENT_ID`, `TIMESTAMP_COLNAME = BUCKET_TS`, target = the metric, exogenous `IN_BED_FRAC`. Unsupervised (`LABEL_COLNAME => ''`). Train on days −14 … −2 (exclude last night so replay is out-of-sample). Detect with `CONFIG_OBJECT => {'prediction_interval': 0.99}`; store `IS_ANOMALY, PERCENTILE, DISTANCE, FORECAST, LOWER_BOUND, UPPER_BOUND`. Severity: 0 inside the interval, scaling to 1 at extreme tails (from PERCENTILE; tune). Chart: model's expected band shaded + anomaly dots.

**③ `SNOWFLAKE.ML.FORECAST`:** on a resident entering HIGH/CRITICAL, train a single-series model on their last 48 h of 15-min RR / SpO₂ / HR and forecast 8 periods (2 h). Dashed line + interval on charts. If it crosses a NEWS2 3-point threshold: "RR forecast to cross 25 by ~03:00." P2: hourly multi-series forecast for all.

**④ Care plans → Cortex Search:** `generate_care_plans.py` makes a 1–2 page PDF per resident (diagnoses, fall precautions, mobility, code status/POLST, baseline cognition & behaviors e.g. "wakes 2× nightly for bathroom", "sundowning", escalation preferences, family contact preferences). Upload to `@DOCS.CARE_PLANS` → `AI_PARSE_DOCUMENT` (LAYOUT) → `SNOWFLAKE.CORTEX.SPLIT_TEXT_RECURSIVE_CHARACTER` (~1000 chars, 150 overlap) → `DOCS.CARE_PLAN_CHUNKS(resident_id, section, chunk)` → `CREATE CORTEX SEARCH SERVICE DOCS.CARE_PLAN_SEARCH ON chunk ATTRIBUTES resident_id …`.

**⑤ AI assessment (Claude via Cortex):** triggers: tier rises to HIGH/CRITICAL; top-5 resident with assessment > 15 min old; Refresh button. Context: profile + meds/flags, baseline-vs-current table, NEWS2 breakdown, anomaly flags, forecast, last 12 h events + notes, top 4 care-plan chunks from Cortex Search (query = situation + "escalation code status fall precautions", filter `resident_id`). Call **REST `POST /api/v2/cortex/inference:complete`** with a JSON-schema `response_format`. Output:
```json
{"headline": "", "assessment": "", "possible_concerns": [], "recommended_checks": [],
 "escalation": "", "care_plan_citations": [{"section": "", "quote": ""}], "confidence": "low|medium|high", "caveats": ""}
```
Prompt guardrails: decision support only, no diagnosis, suggest bedside checks, respect code status, cite the data behind each claim. Fallback: deterministic template from top factors if slow/failing (card never blank). Store in `CORE.AI_ASSESSMENTS` and mirror to Tiger `ai_insights`. Use the best Claude model available in the account (check `SHOW MODELS`/docs at setup); configurable via `CORTEX_MODEL` env.

**⑥ Nurse notes:** text, or voice recorded in browser → `PUT` to `@CORE.NOTE_AUDIO` → `AI_TRANSCRIBE`. Then `AI_EXTRACT` fields: `confusion_change (new|worse|same|better|unknown), oral_intake (normal|reduced|refused|unknown), pain_0_10, urinary_symptoms, fall_or_near_fall, breathing_concern, mood_behavior, concern_level (none|low|med|high), one_line_summary`. Effects: new/worse confusion → NEWS2 consciousness 3; concern_level → P_concern; note on timeline.

**⑦ Shift handoff:** `AI_AGG` over the shift's alerts, checks, notes and score changes with SBAR instructions. Sections: critical events, residents to watch next shift, resolved, device issues, follow-ups; plus one line per flagged resident. Store in `HANDOFFS`.

**⑧ Ask the facility (Cortex Analyst):** semantic model (YAML on `@CORE.SEMANTIC` or a semantic view) over `RESIDENTS, VITALS_15M, EVENTS, CHECKS, ALERTS, NURSE_NOTES`. **REST `POST /api/v2/cortex/analyst/message`**; execute returned SQL; show interpretation, SQL (collapsible), table, auto chart. Verified queries:
- Which residents had more than 3 bed exits last night?
- Average night heart rate for Memory Care over the past week.
- Which residents were checked least often overnight?
- Alerts by unit and tier this week.
- Residents whose respiratory rate rose more than 15% vs their 7-day average.

**⑨ Snowflake REST usage (prize is "Snowflake API"):** Cortex inference, Cortex Search (`POST /api/v2/databases/VIGIL/schemas/DOCS/cortex-search-services/CARE_PLAN_SEARCH:query`), Cortex Analyst all via REST from `app/db/snowflake_rest.py`. SQL-only features (ML functions, AI_EXTRACT, AI_AGG, AI_TRANSCRIBE, AI_PARSE_DOCUMENT) via the Python connector or SQL API (`/api/v2/statements`). The Under-the-Hood page lists every Snowflake API used with call counts.

## 10. Live hardware
**Polar Verity Sense → Mac Chrome via Web Bluetooth** (`/devices` page). `navigator.bluetooth.requestDevice({ filters: [{ services: ['heart_rate'] }], optionalServices: ['fb005c80-02e7-f387-1cad-8acd2d8df0c8'] })` (PMD service listed for P2). Subscribe to `0x2A37` notifications; parse flags (bit0 → uint16 HR, bits1–2 sensor contact, bit4 RR present). Send `{resident_id: 120, ts, hr, worn}` over `WS /ws/ingest`; backend batch-writes to Tiger every second. Band must be in HR mode; no macOS pairing needed; Chrome needs Bluetooth permission in macOS settings. Web Bluetooth does **not** work on iPhone — the band always connects to the Mac.
- R-120 baseline: wear the band for as many hours as possible beforehand; if insufficient, seed history from measured resting HR (be open about it if asked).

**iPhone as bed sensor** (`/bed/[residentId]`): HTTPS required. "Start sensor" button calls `DeviceMotionEvent.requestPermission()` (iOS needs a user tap), plus `navigator.wakeLock.request('screen')` so iOS keeps sending motion. Sample 20–50 Hz, send 1 Hz summaries `{motion_energy, peak_jolt}`. Backend state machine (`devices/bed_state.py`): bed exit = big burst then ≥ 10 s stillness; return = another burst; restlessness levels. Calibrate step (lie still, then shift). Admin "bed exit" button is the fallback.

**Live demo moment:** force night mode; presenter gets up from the "bed" → bed-exit event + HR rise → fall-prevention rule puts R-120 at #1 in seconds → Snowflake card cites "care plan: requires 1-assist, bed alarm."

## 11. Frontend pages
Dark "night shift" theme by default (deliberate: less glare at night); large touch targets; responsive for tablet/phone.
1. **`/` Rounds Queue** — header (facility, display clock, Live/Replay badge, role switcher, staff on duty); KPI strip (count per tier, overdue checks, devices offline); left: ranked cards (name, room, tier chip + score, 3 factor chips, time since check, 2 h HR sparkline, AI headline, **Open** / **Mark checked** quick form); right: floor grid of rooms colored by tier with pulse on CRITICAL; toast + sound on new CRITICAL.
2. **`/resident/[id]`** — header with large code-status badge, age, diagnoses, mobility, Morse; score breakdown bars; AI assessment card (recommended checks as checklist, escalation, expandable care-plan citations, "Snowflake Cortex · model · time", Refresh); synced charts: HR with baseline band p05–p95 + Snowflake expected range + anomaly dots + forecast dashed; RR; SpO₂ with NEWS2 zones; skin-temp change vs baseline; motion bars; bed-presence strip; range 6 h / 24 h / 7 d (7 d reads `vitals_1h`); events/notes timeline; NEWS2 table; Add note (text + mic); Log check.
3. **`/handoff`** — shift picker → Generate → SBAR sections → print.
4. **`/ask`** — Cortex Analyst chat with suggested-question chips.
5. **`/devices`** — Connect Polar (big live HR, status), assign to resident; QR code to phone page; device list with status.
6. **`/bed/[residentId]`** — iPhone sensor page.
7. **`/under-the-hood`** — Tiger + Snowflake proof panels (§13), architecture diagram, benchmark buttons.
8. **`/admin`** — mode toggle, speed, force-night, bookmarks, Prepare replay, scenario injectors, reset, simulated-band fallback.
9. P2 **`/family/[token]`** — plain-language nightly status, no raw vitals.

## 12. Backend API
- `GET /api/health`, `GET /api/residents`, `GET /api/residents/{id}`
- `GET /api/residents/{id}/vitals?metrics=&from=&to=&res=1m|15m|1h`
- `GET /api/queue?as_of=`
- `POST /api/checks`, `POST /api/notes` (multipart: audio or text)
- `GET /api/insights/{id}`, `POST /api/explain/{id}`
- `GET /api/handoff?shift=&date=`, `POST /api/ask`
- `GET /api/stats/tiger`, `GET /api/stats/snowflake`, `POST /api/bench/{name}`
- `POST /api/sim/{start|pause|speed|reset|inject|force_night}`, `POST /api/replay/{prepare|start|seek|stop}`
- `WS /ws/live` (server → client: queue, readings, alerts, insights, sim state), `WS /ws/ingest` (device → server)

Background tasks: live simulator; ingest batch writer (1 s); fast-tier evaluator (2 s); Snowflake sync (1 min); deep-tier detect (per closed 15-min bucket); forecast + assessment on trigger (debounced); replay engine. Baselines run in Tiger via `add_job`.

## 13. Proof for judges (`/under-the-hood`)
- **Tiger:** live ingest rows/s, total rows, compression ratio and before/after size, chunk count, cagg refresh status, **benchmark** "7-day hourly HR for all 120 residents": raw scan vs `vitals_1h`, ms side by side.
- **Snowflake:** 5 anomaly models × 120 series, last detection duration, rows synced, Cortex/REST calls by feature with avg latency, care-plan chunks indexed.
- **Outcome metrics (replay night):** fixed-threshold alarms vs Vigil alerts (fixed = HR > 100 or < 50, RR > 24 or < 10, SpO₂ < 90, any bed exit); lead time for Alvarez (Vigil flag vs scheduled round vs first fixed alarm).

## 14. Scope
- **P0 (demo depends on it):** seed + simulator; Tiger schema/caggs/compression/baselines; fast tier + Rounds Queue; resident charts with baseline band; Snowflake sync + anomaly detection; AI assessment with care-plan citations; Polar live HR; replay + bookmarks; Under-the-Hood; injectors.
- **P1:** nurse notes (text first, then voice); forecast overlay; shift handoff; Cortex Analyst; iPhone bed sensor; alarm-fatigue comparison; Vultr + domain.
- **P2:** family view; Polar ACC/PPI via PMD; suggested round route; unit-level caggs; Cortex Agent combining Analyst + Search.

## 15. Risks to verify early
1. Snowflake trial: Claude model availability in region; `AI_TRANSCRIBE`, `AI_EXTRACT`, `AI_AGG`, `AI_PARSE_DOCUMENT`, Cortex Search, Cortex Analyst, ML functions all enabled.
2. ANOMALY_DETECTION training time for 120 series × 5 models (train ahead).
3. Tiger Cloud plan: storage for ~29M rows; toolkit hyperfunctions available.
4. Verity Sense + Web Bluetooth in Mac Chrome.
5. iPhone bed-exit detection reliability (fallback: admin button).
6. iPhone needs HTTPS (tunnel or Vultr).
