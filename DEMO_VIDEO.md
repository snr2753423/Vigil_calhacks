# Vigil: 3-minute demo video (script + shot list)

The Devpost video is for judges who watch **asynchronously**, including the Snowflake and Tiger sponsor judges. It can show more than the live pitch (Ask, handoff, voice notes), because waits get cut in editing.

**Format:** screen recording with your voice. Record in **7 short clips** (one per section) and join them in iMovie, so a mistake only costs one clip. About 430 words of narration ≈ 3:00 at a calm pace.

Shortcut keys used below: **1–7** bookmarks · **Space** play/pause · **L** back to live · **N** night mode · **B** R-120 bed exit · **G** then **Q/R/U/M** go to Queue / R-120 / Under the hood / Admin · **?** shortcut list.

---

## Before recording (10 min)

1. `make dev` running. Then run the demo reset and wait for **READY — all PASS**:
   ```bash
   make reset
   ```
2. Chrome:
   - A new window with **only** `http://localhost:8080`.
   - Hide the bookmarks bar (**Cmd+Shift+B**).
   - Zoom **110%** (**Cmd +**).
   - Full screen (**Ctrl+Cmd+F**).
3. Click the page once (enables the alert chime), and set the role to **Charge RN**.
4. Mac: turn on **Do Not Disturb**, quit Slack/Messages, and plug in a headset mic if you have one (much clearer than the laptop mic).
5. **R-120 without the band** (until it is charged): go to **Admin** (**G M**) and turn **Simulated band for R-120** ON. You'll use **B** for the bed exit. If the band and iPhone are ready, leave it off and do the real thing in clip 5.
6. Do one silent dry run of all the key presses.

**How to record:** press **Cmd+Shift+5** → **Record Entire Screen** → **Options** → Microphone: your mic → **Record**. Stop with the ■ icon in the menu bar. Each clip saves to the Desktop.

---

## The script

Numbers in **[brackets]**: read them off the screen, since they shift slightly after every reset. As of the last reset: 33 M rows, 14.8× compression, 903 fixed alarms vs 22 Vigil alerts.

### Clip 1 · 0:00–0:20 · The problem
**Screen:** the Rounds Queue, live. Slowly move the mouse over the floor grid.
> "I'm a pre-med engineering student, and this is Vigil. In a nursing home at night, one aide can cover thirty or more residents, walking rounds every two hours. Residents decline *between* those rounds, and in the elderly it's often silent: an infection heading toward sepsis looks like restlessness and a slightly faster heart rate, not a fever. Fixed alarms either miss it, or they ring all night until staff tune them out."

### Clip 2 · 0:20–0:40 · What Vigil does
**Screen:** hover over the top card's reason chips, then the tier chip.
> "Vigil learns each resident's own normal and gives the night shift one ranked list: who to check next, and why. Every reason on these cards is computed, not guessed. It runs at two speeds. **Tiger Data** is the fast tier: it takes in every resident's vitals and scores all 120 of them every two seconds. **Snowflake** is the deep tier: machine-learning anomaly detection against each person's own history, forecasting, and Cortex AI that reads their care plan."

### Clip 3 · 0:40–1:35 · Replay: the silent decline
**Screen:** press **1**. The replay banner appears at 23:12.
> "This is last night's recorded data, replayed through the same pipeline. At 23:12, Rosa Alvarez, 87, with dementia, moves up to Watch. Her heart rate is 77, against *her* expected range of 56 to 69. That range comes from Snowflake's anomaly-detection model, trained on her own history. No fixed alarm would care."

Press **3** (02:14). Pause a beat on the queue.
> "By 2:14 a.m., she's number one, High priority, and still no fixed threshold has fired."

Click her card's **Open**. Hover over the shaded band, an amber ◆ dot, then scroll to the AI card and expand a citation.
> "The shaded band is her personal normal; the dashed range is what Snowflake's model expected; the diamonds are anomalies. This assessment was written by Claude inside Snowflake Cortex. It found her care plan with Cortex Search: 'history of UTIs presenting as confusion.' It suggests bedside checks, not a diagnosis, and every claim is tied to her data."

Press **4** (02:20).
> "Because she was number one, the aide checked on her and found her more confused and warm to touch. That note goes through Snowflake AI_EXTRACT, and new confusion adds three points to her NEWS2 early-warning score."

### Clip 4 · 1:35–1:55 · Forecast and the fast tier
**Screen:** press **5** (Lee). Open his card; point at the dashed forecast line and the callout.
> "Henry Lee has heart failure, and his beta-blocker hides his heart rate. Snowflake ML Forecast predicts his breathing rate will cross the danger line *before* it happens."

Press **7**, then **Space**. Wait for Ortiz to turn CRITICAL with the chime, then **Space** to pause. (Trim the wait in editing.)
> "And for emergencies, like this fall at 4 a.m., the Tiger fast tier puts him at the top within seconds."

### Clip 5 · 1:55–2:20 · Live: I'm resident 120
**Screen:** press **L**, then **N** (moon icon appears).
> "Now we're live. I'm resident 120: hip-fracture history, needs help to walk, high fall risk. My Polar band streams my heart rate over Bluetooth, and my iPhone under the pillow is the bed sensor. It's night."

Get up from the pillow (or press **B**). R-120 jumps to #1 CRITICAL. Press **G R**.
> "I just got out of bed alone, and within seconds I'm number one. Cortex has already pulled my care plan: 'bed alarm, assist one to the bathroom.'"

*(If you used B: you can film yourself standing up with the iPhone camera and drop it in as a picture-in-picture in iMovie.)*

### Clip 6 · 2:20–2:45 · Snowflake for the whole shift
**Screen:** press **L**. Go to **Ask** and click the chip "Which residents had more than 3 bed exits last night?". Show the chart, then open the SQL.
> "For the Director of Nursing, Cortex Analyst answers questions in plain English: here, the generated SQL and the answer straight from Snowflake."

Go to **Handoff**, pick last night, and click **Generate** (cut the wait).
> "And at the end of the shift, Snowflake AI_AGG writes the handoff in SBAR format: the fall, the hospice resident, Rosa's infection workup, and a device that was off for almost four hours."

### Clip 7 · 2:45–3:05 · Proof and close
**Screen:** **G U** (Under the hood). Click the benchmark button and scroll slowly past the Tiger and Snowflake panels and the outcome table.
> "Every number here is a live query. Tiger holds **[33 million]** rows, compressed **[14.8]** times, and a week-long query runs **[35]** times faster on a continuous aggregate. On the same night, fixed thresholds fired **[903]** alarms. Vigil raised **[22]**, and it had Rosa flagged hours before any fixed alarm.
> Vigil is decision support that catches decline earlier. It doesn't diagnose and it doesn't replace staff: it tells one tired aide where to walk next. Tiger for *now*, Snowflake for *what it means*. Thank you."

---

## Editing in iMovie (20–30 min)

1. iMovie → **Create New → Movie**. Drag the 7 clips in order onto the timeline.
2. Trim the dead time: the start/stop of each recording, the ~15 s Cortex and handoff waits, the fall's ~30 s stillness wait. Select a clip edge and drag it, or use **Cmd+B** to split and delete.
3. Optional: add a title card at the start ("Vigil: always-on rounds for nursing homes") and the R-120 iPhone clip as picture-in-picture.
4. **File → Share → File**, 1080p, High quality.
5. **Keep the file on your laptop** (Cal Hacks requires a locally stored video), and upload it to YouTube as **Unlisted** for the Devpost link.

**Target length:** 2:45–3:15. If it runs long, cut Lee (clip 4, first half) and shorten clip 6 to Ask only.
