# Vigil — Build Prompts (paste one phase at a time)

**How to use**
1. Create an empty folder `vigil/`, `git init`, and copy `SPEC.md`, `AGENTS.md`, `CLAUDE.md`, `BUILD_PROMPTS.md` into it. Commit.
2. Do **Phase 0** yourself (accounts + keys).
3. Paste each phase prompt into the suggested tool. Wait for its report, run the acceptance checks yourself, then `git commit -am "phase N"` before starting the next phase. If a phase goes sideways: `git reset --hard` to the last commit and re-prompt with what went wrong.
4. **Default: do everything in Claude Code** (the Claude desktop app's Code tab works). Open the `vigil/` folder as the session's project, paste one phase per session (a fresh session per phase keeps context clean; SPEC.md/AGENTS.md carry the context). Preview the app in the built-in browser pane for UI tweaks, and run `/code-review` after each phase instead of a separate reviewer.
   *Optional extras:* Cursor for hands-on visual UI polish; Codex as a second-opinion reviewer. Never let two agents edit the same files at the same time.
5. **Practice run vs the real event:** this kit (spec + prompts) is ideation and can be reused. At Cal Hacks, start a **fresh repo** and rebuild from the prompts — don't copy practice-run code, since all implementation must happen during the hackathon.

Estimated hackathon timeline (36 h): P1–P3 by hour 8 · P4 by hour 13 · P5–P6 by hour 20 · P7–P8 by hour 26 · P9 by hour 31 · P10 + rehearsal in the last 5 h. Sleep ~3 h somewhere after P8.

---

## Phase 0 — You, not an agent (~45 min)
- [ ] **Snowflake:** sign up through the MLH student trial link on the Devpost Snowflake prize (120-day trial). Edition **Enterprise**, cloud **AWS**, region **US West (Oregon)**. Note the account identifier (`orgname-accountname`).
- [ ] **Tiger Cloud:** sign up, create a service (Time-series / TimescaleDB), copy the connection string. Check whether MLH gives credits at the event.
- [ ] **Vultr:** create an account (MLH usually provides credits at the event).
- [ ] **Polar Verity Sense:** order now; charge it; in Chrome on your Mac open `chrome://bluetooth-internals` once to confirm Bluetooth works. In macOS System Settings → Privacy & Security → Bluetooth, allow Chrome.
- [ ] **Tools:** install Docker Desktop, `uv` (`brew install uv`), Node 20+ and `pnpm`, `cloudflared` (`brew install cloudflared`).
- [ ] **iPhone:** Safari is fine. Phase 7 needs an HTTPS URL (tunnel) — no app install.
- [ ] Copy `.env.example` to `.env` after Phase 1 creates it, and fill in keys (Phase 1 prompt tells the agent to generate the Snowflake key-pair and give you the exact SQL to run).

---

## Phase 1 — Scaffold, infra, connection checks  ·  Claude Code
```
Read AGENTS.md and SPEC.md §5 (stack, repo layout) and §12 (API list).

Build the project skeleton exactly per the repo layout in SPEC.md §5:
- backend/ with uv + FastAPI app, config.py using pydantic-settings reading .env, GET /api/health, a WebSocket hub stub at /ws/live that broadcasts a heartbeat every 2 s.
- frontend/ with Next.js App Router + TypeScript + Tailwind + shadcn/ui + ECharts + Zustand; dark theme by default; a placeholder Rounds Queue page that connects to /ws/live and shows the heartbeat.
- docker-compose.yml + Caddyfile: Caddy on :8080, /api/* and /ws/* → backend:8000, everything else → frontend:3000. Also support running backend/frontend locally without Docker (document both).
- Makefile with: dev, check, migrate, seed, test, tunnel (see AGENTS.md). migrate/seed can be stubs for now.
- .env.example with: TIGER_DATABASE_URL, SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY_PATH, SNOWFLAKE_ROLE, SNOWFLAKE_WAREHOUSE=VIGIL_WH, SNOWFLAKE_DATABASE=VIGIL, CORTEX_MODEL, SEED_DAYS=3, FACILITY_TZ=America/Los_Angeles.
- scripts/check_connections.py: connect to Tiger and print TimescaleDB + timescaledb_toolkit versions (create extension timescaledb_toolkit if missing); connect to Snowflake with key-pair auth, print current role/warehouse, list available Cortex models if possible, and run a one-line AI_COMPLETE with CORTEX_MODEL. Print a clear PASS/FAIL table.
- Snowflake key-pair: generate an RSA key pair into ./secrets/ (gitignored) and print the exact `ALTER USER ... SET RSA_PUBLIC_KEY='...'` SQL for me to run in Snowsight. Also print the SQL for me to create warehouse VIGIL_WH (XSMALL, AUTO_SUSPEND=60) and database VIGIL with schemas CORE, ML, DOCS.
- README with setup steps and a Status section.

Acceptance: `make dev` serves http://localhost:8080 showing the live heartbeat; `make check` prints PASS for Tiger and Snowflake (after I fill .env and run the printed SQL); `make test` runs (even if only a trivial test).
Stop after this phase and report.
```

---

## Phase 2 — Tiger schema, simulator, seed  ·  Claude Code
```
Read AGENTS.md and SPEC.md §2, §3, §4, §6, §8.

1. Tiger migrations in backend/sql/tiger/ (idempotent): residents, staff, devices, vitals hypertable (wide columns per §6, 1-day chunks, columnstore/compression segmentby resident_id orderby ts DESC), events hypertable, priority_snapshots hypertable, resident_baselines, ai_insights, sync_state. Continuous aggregates vitals_1m (real-time aggregation ON) → vitals_15m (with percentile_agg for hr) → vitals_1h. Refresh, compression (after 1 day), and retention (raw 90 d, caggs 2 y) policies. An add_job hourly baseline procedure per §6 using percentile_agg/rollup/approx_percentile and state_agg for longest still period. `make migrate` applies them in order.
2. Simulator (app/sim/physiology.py, cast.py): per-resident stable random params from a seeded RNG; normal physiology per §8 (circadian rhythm, activity, bed schedule, bathroom trips, turning cadence, band charging gaps, AFib/beta-blocker/COPD variants); staff events (round checks ±20 min, meals/fluids, BP + core temp spot checks 2×/day). Resident roster: 120 residents across the 3 units with realistic names/ages/diagnoses/meds/Morse/mobility/cognition/code status; the scripted cast IDs 17, 42, 101, 110, 88, 91, 33, 120 exactly as in §8.
3. Scripted "last night": the night before the seed run (22:00–06:30 facility time), apply the cast scripts from §8 including Alvarez's slow drift starting the previous afternoon and her 21:30 CNA note event. Tune so no fixed threshold (HR>100, RR>24, SpO2<90) fires for Alvarez before ~03:50.
4. scripts/seed.py (`make seed DAYS=N`): truncate + regenerate N days of history ending now, bulk insert with COPY, refresh caggs for the whole range, run the baseline job once, then compress all eligible chunks. Print rows inserted, elapsed time, and compression ratio.
5. Live loop (app/sim/live_loop.py): on backend startup, backfill from the last stored ts to now, then stream all simulated residents every 5 s in real time (batch writes). R-120 is NOT simulated in live mode unless the "simulated band" fallback flag is on.
6. Tests for physiology ranges and the cast scripts (e.g., Alvarez's HR at 02:00 is above her own baseline p95 but below 100).

Acceptance: `make seed DAYS=3` completes; SQL shows ~6M vitals rows, caggs populated, compression ratio printed; querying vitals_1h for 7 days is fast; resident_baselines has rows for all 120 residents; live loop keeps inserting new rows every 5 s.
Stop and report.
```

---

## Phase 3 — Scoring engine (fast tier) + queue API  ·  Claude Code
```
Read AGENTS.md and SPEC.md §4 (as_of), §7 (all), §12.

Implement app/scoring/: news2.py (full table incl. COPD Scale 2), rules.py (5 critical rules, fall-prevention rule incl. force_night, device-issue rule using heartbeat_agg or plain-SQL fallback), baseline.py (z-scores vs resident_baselines by 4-hour block), priority.py (exact formula, +10 multi-signal boost, tiers, top-3 deterministic factor strings with human-readable text like "HR +22% vs her night normal"), evaluator.py (every 2 s, as_of-driven, efficient batched SQL over vitals_1m / latest readings for all 120 residents; hysteresis, persistence, 30-min suppression after check_logged; writes priority_snapshots; emits alert_raised events on tier escalation).
P_anom uses Tiger z-score fallback for now (Snowflake comes in Phase 5); P_trend = 0 for now.
API: GET /api/queue?as_of=, GET /api/residents, GET /api/residents/{id}, GET /api/residents/{id}/vitals (res=1m|15m|1h, time_bucket_gapfill + locf for charts), POST /api/checks. /ws/live broadcasts queue updates and new-alert messages.
Sim state endpoints: POST /api/sim/{pause|start|force_night|inject} with injectors from SPEC §8 (fall, sepsis drift, remove band, bed exit).
Unit tests: every NEWS2 band edge, each critical rule, priority formula, hysteresis/persistence/suppression.

Acceptance: GET /api/queue?as_of=<last night 02:14 local> ranks Alvarez (17) in the top 3 with sensible factors; injecting a fall for resident 5 makes it CRITICAL within ~5 s on /api/queue; evaluator loop stays under 1 s per cycle (log timing).
Stop and report.
```

---

## Phase 4 — Core UI: Rounds Queue + Resident detail + Admin  ·  Claude Code (then Cursor for polish)
```
Read AGENTS.md and SPEC.md §11 (pages 1, 2, 8), §7d (tiers/factors).

Build:
- `/` Rounds Queue: header (facility, display clock honoring force_night offset, Live/Replay badge, role switcher), KPI strip, ranked cards (tier chip with icon + color, score, 3 factor chips, time since check, 2 h HR sparkline, AI headline placeholder, Open + Mark checked quick form), floor grid of 120 rooms by unit colored by tier with a pulse on CRITICAL, toast + sound on new CRITICAL. Live via /ws/live with smooth reordering animations.
- `/resident/[id]`: header with big code-status badge; score breakdown bars; AI assessment card placeholder (renders deterministic fallback text); ECharts synced charts per SPEC §11.2 (HR with personal baseline p05–p95 band; RR; SpO2 with NEWS2 zones; skin temp change vs baseline; motion bars; bed-presence strip); range 6h/24h/7d; events timeline; NEWS2 table; Log check; Add note (text only for now, stored as event).
- `/admin`: pause/start, force night, scenario injectors, reset.
Design: dark night-shift theme, large touch targets, readable at arm's length on an iPad, accessible (icons + text, not color only). Keep components small and typed.

Acceptance: queue updates live without page refresh; clicking a card opens detail with all charts populated from real Tiger data; injecting a fall visibly moves that resident to the top with a toast; works at iPad width and phone width.
Stop and report.
```
*Then in Cursor:* open the running app and iterate visually ("make the cards denser", "the floor grid should look like a building map", etc.).

---

## Phase 5 — Snowflake core: sync + anomaly detection  ·  Claude Code
```
Read AGENTS.md and SPEC.md §9 (①, ②, ⑨) and §7d (P_anom).

1. backend/sql/snowflake/001–002: tables in VIGIL.CORE per §9 (idempotent). A script/Make target applies them via the connector.
2. app/deep/sync.py: bulk-load all historical vitals_15m + residents/staff/events once (called at the end of `make seed`), then every minute sync closed buckets newer than the watermark plus new events/checks/alerts/notes via write_pandas; track in sync_state; count rows synced.
3. scripts/train_models.py + sql/snowflake/004_models.sql: create 5 multi-series SNOWFLAKE.ML.ANOMALY_DETECTION models (HR_AVG, RR_AVG, SPO2_MIN, SKIN_TEMP_AVG, MOTION_SUM) with SERIES_COLNAME RESIDENT_ID, TIMESTAMP_COLNAME BUCKET_TS, exogenous IN_BED_FRAC, trained on days -14..-2 (exclude last night). Print training time per model.
4. app/deep/anomaly.py: on each newly closed 15-min bucket (and on demand for a time range), call DETECT_ANOMALIES with prediction_interval 0.99 for all residents; write ANOMALY_RESULTS; compute severity per SPEC; mirror to Tiger ai_insights (kind=anomaly). Plug into priority.py as P_anom (Tiger z fallback when no result).
5. Resident charts: overlay Snowflake expected range (LOWER/UPPER_BOUND) and anomaly dots.
6. Instrument every Snowflake call (feature, latency) for /api/stats/snowflake.

Acceptance: `make seed` then `python scripts/train_models.py` succeed; running detection over last night flags Alvarez on HR/skin temp/motion before 02:14 and does NOT flag Rossi (AFib) or Kim (COPD); the UI shows anomaly dots on Alvarez's chart; if Snowflake creds are removed, the app still runs with Tiger fallback.
Stop and report.
```

---

## Phase 6 — Care plans + Cortex Search + AI assessment  ·  Claude Code
```
Read AGENTS.md and SPEC.md §9 (④, ⑤, ⑨), §11.2 (AI card).

1. scripts/generate_care_plans.py: one 1–2 page PDF per resident (reportlab) with the sections in SPEC §9④, consistent with each resident's profile; the cast must have specific, quotable lines (Grant: "bed alarm; assist ×1 to bathroom"; Patel: POLST comfort-focused, no CPR, notify hospice + daughter; Alvarez: FULL code, history of UTIs presenting as confusion, notify provider for acute mental status change).
2. sql/snowflake/003_docs.sql: stage with directory; upload PDFs (PUT); AI_PARSE_DOCUMENT (LAYOUT) → SPLIT_TEXT_RECURSIVE_CHARACTER → DOCS.CARE_PLAN_CHUNKS(resident_id, section, chunk) → CORTEX SEARCH SERVICE DOCS.CARE_PLAN_SEARCH with resident_id attribute.
3. app/db/snowflake_rest.py: JWT key-pair auth; methods cortex_complete(messages, response_format), search_care_plan(resident_id, query, limit=4), analyst_message(...) (stub for Phase 9).
4. app/deep/assess.py: build the context per SPEC §9⑤, call REST inference:complete with the JSON schema, validate, store in CORE.AI_ASSESSMENTS + Tiger ai_insights; triggers + debounce per SPEC; deterministic fallback on timeout (8 s) or error.
5. UI: AI card shows headline, assessment, recommended checks checklist, escalation, expandable care-plan citations, "Snowflake Cortex · <model> · <time>", Refresh; queue cards show the AI headline.

Acceptance: Alvarez's assessment at as_of 02:14 mentions possible infection/delirium, cites her care-plan line about UTIs presenting as confusion, and recommends bedside checks; Patel's recommends comfort-focused action and cites the POLST; killing network to Snowflake shows the fallback text, not an error.
Stop and report.
```

---

## Phase 7 — Live hardware: Polar band + iPhone bed sensor  ·  Claude Code
```
Read AGENTS.md and SPEC.md §10, §11 (pages 5, 6), §7a (fall-prevention rule).

1. `/devices`: "Connect Polar" button using Web Bluetooth per SPEC §10 (heart_rate service, 0x2A37 notifications, parse flags incl. 16-bit HR and sensor contact); big live HR number, connection state, auto-reconnect; assign to resident 120; stream over /ws/ingest. Pure function for BLE parsing with vitest unit tests (8-bit, 16-bit, contact bits, RR present).
2. Backend app/devices/ingest.py: /ws/ingest accepts {resident_id, ts, hr, worn} and {resident_id, motion_energy, peak_jolt}; batch-write to vitals every 1 s with source=1 (polar) or 2 (phone).
3. `/bed/[residentId]` iPhone page: Start button → DeviceMotionEvent.requestPermission() → wakeLock → sample motion, send 1 Hz summaries; calibrate flow (lie still 10 s, then shift); show live motion meter and detected state.
4. app/devices/bed_state.py: state machine per SPEC §10 producing bed_exit / bed_return events and in_bed/bed_motion values for resident 120; thresholds derived from calibration; unit tests with recorded-style synthetic traces.
5. `/devices` shows a QR code for the HTTPS /bed/120 URL (read PUBLIC_URL from env, set from the tunnel).
6. Admin: "simulated band for R-120" fallback toggle and "bed exit R-120" button.

Acceptance (I will test with the real hardware): band HR appears on /devices and on R-120's chart within 2 s; with force_night on, getting up from the bed (phone under the mattress/pillow) triggers bed_exit and R-120 jumps to #1 (score 90) within ~5 s, and the AI card cites the care plan's bed-alarm/assist line.
Stop and report.
```

---

## Phase 8 — Replay mode + Under-the-Hood  ·  Claude Code
```
Read AGENTS.md and SPEC.md §4 (replay, prepare, bookmarks), §11 (pages 7, 8), §13.

1. app/sim/replay.py: cursor over last night with speed 30/60/120×, play/pause/seek, bookmarks per SPEC §4; the evaluator runs as_of the cursor; /ws/live sends cursor time + queue; UI banner "Replaying last night's recorded data through the live pipeline"; Live/Replay badge.
2. scripts/prepare_replay.py + POST /api/replay/prepare: run DETECT_ANOMALIES over last night's buckets for all residents; run forecasts for residents that reach HIGH/CRITICAL at each step; generate and cache AI assessments at each bookmark and on each tier escalation; store everything keyed by as_of so replay only reveals results with ts <= cursor.
3. `/under-the-hood`: Tiger panel (ingest rows/s, total rows via approximate_row_count, compression ratio + before/after size, chunk count, cagg/job status) with a benchmark button "7-day hourly HR for all 120 residents: raw vs vitals_1h" showing both timings; Snowflake panel (models × series, last detection duration, rows synced, calls by API/feature with avg latency, care-plan chunks indexed); outcome metrics from the replay night: fixed-threshold alarm count vs Vigil alerts, and Alvarez lead time (Vigil HIGH vs her scheduled round vs first fixed alarm); a simple architecture diagram (SVG) showing fast tier vs deep tier.

Acceptance: from /admin, Prepare replay then play at 60× from 01:45: Alvarez climbs and becomes #1 at 02:14 with her cached AI assessment; bookmarks jump correctly; under-the-hood numbers are real queries, not hardcoded.
Stop and report.
```

---

## Phase 9 — P1 Snowflake features: forecast, notes, handoff, Ask  ·  Claude Code
```
Read AGENTS.md and SPEC.md §9 (③, ⑥, ⑦, ⑧), §11 (pages 2, 3, 4).

1. app/deep/forecast.py: on HIGH/CRITICAL, single-series SNOWFLAKE.ML.FORECAST on the resident's last 48 h (RR, SpO2, HR), 8 periods; store FORECAST_RESULTS + ai_insights; set P_trend; chart dashed forecast + interval and a "RR forecast to cross 25 by ~03:00" callout.
2. app/deep/notes.py + POST /api/notes: text → AI_EXTRACT fields per SPEC §9⑥; audio (webm/m4a from the browser mic; works on iPhone Safari over HTTPS) → PUT to @CORE.NOTE_AUDIO → AI_TRANSCRIBE → AI_EXTRACT. Apply effects (NEWS2 consciousness, P_concern). UI: mic button with recording state, show transcript + extracted chips.
3. app/deep/handoff.py + `/handoff`: AI_AGG with SBAR instructions over the shift's alerts/checks/notes/score changes; sections per SPEC; print-friendly.
4. app/deep/analyst.py + `/ask`: semantic model YAML (or semantic view) with the 5 verified queries from SPEC §9⑧; REST analyst message → execute SQL → table + auto chart; show interpretation and collapsible SQL; suggested-question chips.

Acceptance: each verified question returns a correct table; a spoken note "she seems more confused tonight and barely drank" raises that resident's NEWS2 consciousness to 3 and her score; handoff for last night names Alvarez, Lee, Patel, Ortiz, and the Wright device issue.
Stop and report.
```

---

## Phase 10 — Deploy, harden, demo assets  ·  Claude Code (+ Codex review)
```
Read AGENTS.md and SPEC.md §5 (hosting), §13, §14.

1. Deploy to a Vultr VM with docker compose; Caddy auto-HTTPS on my domain (I'll give it); PUBLIC_URL set so the iPhone QR code works without a tunnel. Write DEPLOY.md.
2. scripts/reset_demo.py: re-seed (14 days), resync Snowflake, train models, prepare replay, clear notes/checks from the live session — one command, prints a READY checklist.
3. Demo hardening: preload caches on startup; all pages load in < 1 s on venue wifi; no console errors; offline-safe fallbacks for every Snowflake feature; a hidden keyboard shortcut panel for demo control (play replay, jump to bookmark, toggle force night, inject bed exit).
4. README for Devpost: problem, how it works (fast tier vs deep tier), exactly how Tiger Data and Snowflake are used (bullet list mapping features → files), screenshots, setup.

Acceptance: fresh `reset_demo.py` → full demo runs end to end on the public URL; iPhone bed page works over the domain.
```
*Then in Codex:* "Review this repo against SPEC.md. List bugs, demo risks, and any place where Tiger and Snowflake roles blur. Don't change code; give a prioritized list." Feed the list back to Claude Code.
