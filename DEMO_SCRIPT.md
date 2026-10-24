# Vigil: 3-minute demo script

The demo has three parts:
1. Replay last night (the Snowflake deep tier finds Rosa Alvarez's silent decline hours early).
2. The live R-120 moment (the Tiger fast tier puts the presenter at #1 in seconds).
3. Under the hood (proof).

Every step has a keyboard shortcut. Press **?** on any page to see them.

| Key | Action | Key | Action |
|---|---|---|---|
| **1–7** | Jump to a bookmark (from live: opens the replay there, paused) | **N** | Toggle force night |
| **Space** | Play / pause the replay | **B** / **Shift+B** | R-120 bed exit / back in bed |
| **S** | Speed 30× → 60× → 120× | **L** | Back to live |
| **G** then **Q / R / U / M** | Go to Queue / R-120 / Under the hood / Admin | **?** | Shortcut panel |

Bookmarks: **1** 23:12 Alvarez first deviation · **2** 01:34 Alvarez reaches HIGH · **3** 02:14 Alvarez at #1 · **4** 02:20 Alvarez confusion noted · **5** 02:30 Lee forecast warning · **6** 03:20 Patel immobility · **7** 04:05 Ortiz fall.

## Pre-flight

**T−60 min**
- Run `scripts/deploy.sh reset` (add `--simulated-band` if the Polar band or iPhone isn't working). It must print **READY — all PASS**. A WARN on "Backend" means your laptop's `make dev` is running: stop it.

**T−15 min, Mac (Chrome, full screen, zoom 110–125 % so the back row can read it)**
- Open `https://<DOMAIN>`.
- Click once anywhere: browsers only play the alert chime after a click. Check that the speaker icon is on.
- Set the role to **Charge RN**.

**T−15 min, hardware**
- **Polar band:** in HR mode, on the wrist. On **Devices**, click **Connect Polar**. Big HR number, "On the wrist".
- **iPhone:** turn Low Power Mode off and the volume up, and set Auto-Lock to Never.
- Scan the QR code on **Devices**, then in Safari tap **Start sensor** → **Allow** → **Calibrate** (~35 s, spoken).
- Lie down once to check it says "in bed".

**T−5 min**
- Press **L** (live).
- Make sure night mode is **off**. You turn it on during the demo, so the audience sees the switch.
- Close the shortcut panel.

**Backup**
- Keep a screen recording of a full run, made the night before, on the desktop.
- Have the local stack ready (`make dev` or `make up`) in case the venue wifi dies (see Backups).

## The script (3:00)

### 0:00–0:20 · The problem (Rounds queue, live)
> "At 3 a.m. in a nursing home, one aide covers thirty-plus residents and walks rounds every two hours. People decline *between* rounds, and in the elderly it's silent. An infection heading for sepsis looks like restlessness and a slightly faster heart rate, not a fever. Fixed alarms miss it, or they ring all night until staff tune them out."

### 0:20–0:35 · What Vigil is
> "Vigil learns each resident's own normal and gives the night shift one ranked list: who to check next, and why. Two speeds. **Tiger Data** is the fast tier, scoring every resident every two seconds. **Snowflake** is the deep tier: anomaly detection against each person's own baseline, forecasts, and Cortex AI that reads their care plan."

### 0:35–1:35 · Replay last night
Press **1**. The replay banner appears at 23:12.
> "This is last night's recorded data, replayed through the live pipeline. 23:12: Rosa Alvarez, 87, moderate dementia, moves to the top as WATCH. Her heart rate is 77 against *her* expected 56 to 69. That's Snowflake's anomaly model judging her against her own history, and no fixed alarm would care."

Press **3** (02:14).
> "02:14: she's **#1, HIGH**. Still no fixed threshold has fired."

Click her card (**Open**). Point at the shaded Snowflake band, the ◆ anomaly dots, and the AI card.
> "Claude in Snowflake Cortex wrote this from her numbers and her care plan, which it found with Cortex Search: 'History of UTIs presenting as confusion. Notify provider for acute mental status change.' The checks it suggests are bedside checks, and every claim cites the data."

Press **4** (02:20).
> "The aide went because she was #1, and found her more confused and warm to touch. The note goes through Snowflake `AI_EXTRACT`; new confusion adds three NEWS2 points."

Press **5** (02:30). Point at Henry Lee.
> "Meanwhile Henry Lee, heart failure, on a beta-blocker that hides his heart rate. Snowflake `ML.FORECAST` says his breathing rate will cross 25 *before* it does."

Press **7** (the replay snaps to 04:04, paused), then **Space**. About 2–4 s later (one or two 2-minute steps at 60×), James Ortiz goes CRITICAL 90 (out of bed alone), then CRITICAL 100 "Suspected fall", with a chime. **Press Space again to hold it.** His 04:11 check clears him about 8 s later, and Arthur Patel (comfort care, possibly unresponsive) takes #1.
> "And when it's an emergency, a fall, the fast tier in Tiger has him at the top in seconds."

### 1:35–2:15 · Live: the presenter is R-120
Press **L**, then **N** (the moon icon appears).
> "Now live. I'm resident 120: hip fracture history, one-assist, high fall risk. My Polar band streams my heart rate over Bluetooth; the phone under this pillow is my bed sensor. It's 'night'."

Lie down, wait for "in bed" (or skip if already in bed), then **get up and walk away**.
> *(about 4 s later)* "Out of bed, at night, without staff: **#1, CRITICAL**."

Press **G R** (R-120's page).
> "And Cortex already pulled my care plan: 'bed alarm, assist ×1 to bathroom'."

Afterwards, click **Mark checked** on R-120's card so the alert clears. Going back to bed alone holds the fall-prevention rule for 5 more minutes.

### 2:15–2:45 · Under the hood
Press **G U**.
> "Every number here is a live query. Tiger: 23 million rows, compressed **14.7×**. A 7-day hourly query for all residents: 5 seconds from raw rows, **0.2 seconds** from the continuous aggregate. Snowflake: five anomaly models × 120 residents, Cortex Search over 1,200 care-plan chunks, and every API call counted. On last night: **903 fixed-threshold alarms, 22 Vigil alerts.** Vigil had Alvarez at HIGH **3 hours 20 minutes** before the first fixed alarm."

### 2:45–3:00 · Close
> "Vigil is decision support that catches decline earlier. It doesn't diagnose and doesn't replace staff; it tells one tired aide where to walk next. Tiger for *now*, Snowflake for *what it means*. Thank you."

## Backup plan

| Step | If this fails | Do this instead |
|---|---|---|
| Venue wifi / server | Page won't load or "Disconnected" | Phone hotspot. If the server itself is down, run `make up` (or `make dev`) on the Mac and use `http://localhost:8080`. Replay never calls Snowflake, so it works on any connection that reaches Tiger. |
| Snowflake slow or down | AI card says "showing the template" | Keep talking. The template is deterministic and still quotes the care plan. Ask shows the last real answer, labelled. Under the hood shows its cached inventory. Say: "Snowflake is the deep tier; the fast tier never depends on it." |
| Replay won't start | "Run Prepare replay first" | Run `scripts/deploy.sh reset` (about 5 min if only the cards are missing). In the meantime, show the live queue and Under the hood. |
| Polar band | No HR / won't pair | On Admin (**G M**), turn on **Simulated band for R-120**. The live moment still works with **B**. |
| iPhone bed sensor | No exit detected | Press **B** when you stand up. It goes through the same bed-sensor path. **Shift+B** puts him back in bed. |
| Night mode | R-120 doesn't go to 90 | Night mode is off: press **N** and check for the moon icon. A check logged in the last 5 min also suppresses it: wait or pick another resident. |
| Chime silent | No sound on CRITICAL | Click the page once (browser autoplay rule) and check the speaker icon in the header. |
| Running long | Over 2:30 at the live step | Skip bookmark 5 (Lee) and go straight from 4 to 7. Cut Under the hood to one sentence. |
| Total failure | Nothing works | Play the backup screen recording and narrate over it. |

## Likely judge questions

**Why two databases?**
Different jobs. Tiger is the operational store: high-rate ingest, continuous aggregates, rules every 2 s. Snowflake is the intelligence layer: ML models over all residents, Cortex AI, search over documents, natural-language analytics. Each tier fails independently: if Snowflake is down, the fast tier and the UI keep working.

**Is this real patient data?**
No. It's 100 % synthetic, from a seeded simulator (120 residents, 10 days, ~2 M rows/day), plus my own live band and phone. There are no cameras anywhere.

**How is this better than thresholds?**
Each resident is judged against their own normal. Kim has COPD: thresholds fired 18 alarms for her last night, Vigil none. Alvarez never crossed a fixed threshold until 04:02, and Vigil had her HIGH at 00:42. Over the whole night: 903 fixed alarms vs 22 Vigil alerts.

**How do you avoid alarm fatigue?**
- Hysteresis: in at 60, out at 50.
- Persistence: 2 consecutive evaluations, except emergencies.
- 30-minute suppression after a check unless the score rises 10+.
- Device problems are tasks, never alarms.

**Does the AI diagnose?**
No. The prompt forbids it, and a wording guard rewrites "likely UTI" to "possible UTI" and rejects what it can't fix. Any care-plan citation that isn't in the retrieved text is dropped. The score and its top 3 reasons are deterministic and never AI-generated. Code status changes the recommended action, never the score.

**What does Snowflake ANOMALY_DETECTION actually do here?**
Five multi-series models, one series per resident, trained on about 9 days with last night held out. Each closed 15-minute bucket gets an expected value and a 99 % interval per resident. Outside the interval becomes a severity from 0 to 1, which feeds a quarter of the score.

**How fast is it?**
- Bed exit → #1: about 4 s.
- Fall: about 35 s (the rule needs 30 s of stillness after the impact).
- A Cortex card: about 15 s, with the template shown instantly meanwhile.
- Anomaly results: about 8–10 min after each 15-minute bucket closes.

**What did you build vs. use?**
- Built: scoring (NEWS2, rules, priority, alert gate), the simulator and scripted night, sync, replay, the device pipeline and the UI.
- Used: Tiger's hypertables, continuous aggregates and hyperfunctions; Snowflake's ML functions, Cortex AI, Search and Analyst through their REST APIs.

**Would this scale?**
120 residents is about 2 M rows/day. Compression is 14.7×, and charts and baselines read the continuous aggregates (24× faster than raw). Snowflake models are multi-series, so one model covers every resident.

**What about HIPAA / privacy?**
Out of scope for a hackathon with synthetic data. A real deployment would need HIPAA-eligible accounts with BAAs, access control (today it's a role switcher with no login) and audit logging.

**How accurate is the forecast?**
It's coarse, and I'll be honest about it. At 02:30 it said Lee's RR would cross 25 by about 04:15; it actually did around 03:00. It warns early enough to matter, but it's a trend extrapolation, not a clock.

**Why did training take 67 minutes?**
Five models × 120 series × 9 days of 15-minute buckets on a trial warehouse. It's trained once ahead of time, and detection on a new bucket takes a few minutes.
