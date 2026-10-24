"""Cortex AI assessment with care-plan citations + deterministic fallback (SPEC §9 ⑤).

Pipeline for one resident `as_of` a time (live: now; replay/API: any past time):

1. `build_context`: profile + meds/flags, the deterministic score (factors, NEWS2 breakdown), each metric's
   latest values vs the Snowflake model's expected range and her Tiger personal baseline, the last 8 h
   hourly trend, the last 12 h of events and notes, the forecast if one exists, and the top 4 care-plan
   chunks from Cortex Search (REST, filtered to the resident).
2. `messages_for`: guarded system prompt + the context as plain text.
3. Cortex completion (REST, `snowflake_rest.cortex_complete`) with the JSON schema below as response_format.
4. `validate`: shape, enums and lengths; care-plan citations are kept only if the quote really appears in
   the retrieved chunks (the model can't invent a care-plan line).
5. Store in Snowflake CORE.AI_ASSESSMENTS and mirror to Tiger ai_insights (kind=assessment), broadcast an
   `insight` message on /ws/live.

`template_assessment` is the fallback the card shows whenever no fresh AI assessment exists or Snowflake is
slow/down (AGENTS.md rule 5): built only from the deterministic factors, the code status and the resident's
care-plan profile, in the same JSON shape as the Cortex output. Callers wait at most ASSESS_TIMEOUT_S (8 s);
a slower Cortex call keeps running and its result replaces the template when it lands.

Triggers: a live alert (the evaluator calls `Assessor.on_alert` the moment it raises one, e.g. R-120's live
bed exit, so the card is on its way within the same second); every SCAN_INTERVAL_S in live mode
(`Assessor.run`), a tier rise into HIGH/CRITICAL the alert path missed and a top-5 (non-STABLE) resident whose
assessment is > 15 min old; the card's Refresh button (`POST /api/explain/{id}`). Debounced: one call in
flight per resident, automatic triggers at most every 30 s (new rule) / 2 min (tier rise) / 15 min (stale).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from app.config import Settings, get_settings
from app.db import tiger
from app.db.snowflake_rest import client as rest_client
from app.deep.anomaly import REVEAL_DELAY
from app.deep.sync import BUCKET, naive_utc
from app.deep.wording import soften, violations
from app.scoring import evaluator as ev
from app.scoring.baseline import BLOCK_NAMES, VITAL_METRICS, Baselines, tod_block
from app.scoring.priority import TIER_RANK
from app.scoring.trend import annotate as annotate_forecast
from app.sim.care_plans import MOBILITY_TEXT
from app.ws.hub import hub

log = logging.getLogger(__name__)

TEMPLATE_MODEL = "deterministic template"
FORECAST_FRESH = timedelta(minutes=tiger.FORECAST_LOOKBACK_MIN)  # the forecast P_trend still uses

# Bedside checks suggested by each factor key (rule:* keys use the part after the colon).
CHECKS: dict[str, str] = {
    "fall": "Go to the resident now; check for injury before helping them up",
    "fall_prevention": "Go to the room now and assist; check toileting needs and pain",
    "unresponsive": "Check responsiveness, breathing and pulse at the bedside now",
    "hr_extreme": "Recheck pulse manually and full vital signs",
    "spo2_low": "Recheck SpO₂ with a finger probe; check breathing and colour",
    "rr_extreme": "Count respirations for a full minute; check work of breathing",
    "hr": "Recheck pulse and full vital signs",
    "rr": "Count respirations for a full minute",
    "spo2": "Recheck SpO₂ and breathing",
    "sbp": "Repeat blood pressure",
    "temp": "Recheck core temperature",
    "skin_temp": "Check temperature and how warm the skin feels",
    "motion": "Look for restlessness, pain or discomfort",
    "concern": "Compare mental status with their usual cognition; check fluids and toileting",
    "bed_exits": "Check toileting needs, pain and the bed alarm",
    "still": "Check responsiveness and reposition",
    "overdue": "Complete a bedside check",
    "device_offline": "Check the wristband is on, charged and in range",
    "band_off": "Put the wristband back on if the resident agrees",
    "device": "Check the wristband is on, charged and in range",
}

CODE_STATUS_ESCALATION = {
    "FULL": "Full code: escalate to the charge nurse; call the provider for any acute change.",
    "DNR": "DNR: escalate to the charge nurse; transfer and treatment per care plan, no CPR.",
    "DNR-DNI": "DNR-DNI: escalate to the charge nurse; no CPR or intubation, other treatment per care plan.",
    "COMFORT": "Comfort care: focus on comfort; notify hospice and family per care plan. No CPR.",
}

TIER_HEADLINE = {
    "CRITICAL": "Go now",
    "HIGH": "Check within 15 min",
    "WATCH": "Check first on the next round",
    "STABLE": "Routine rounds",
}


def _check_key(factor_key: str) -> str:
    return factor_key.removeprefix("rule:")


def template_assessment(resident: dict, current: dict | None) -> dict:
    """The AI card's content from deterministic inputs only (never invents a finding)."""
    code = str(resident.get("code_status") or "FULL")
    profile = resident.get("profile") or {}
    if current is None:
        return {
            "headline": "No current score",
            "assessment": "Vigil has no recent readings to score for this resident.",
            "possible_concerns": [],
            "recommended_checks": ["Complete a bedside check"],
            "escalation": CODE_STATUS_ESCALATION.get(code, ""),
            "care_plan_citations": [],
            "confidence": "low",
            "caveats": "No data to summarise.",
            "source": "template",
            "model": TEMPLATE_MODEL,
        }
    tier = current["tier"]
    factors = current.get("factors") or []
    texts = [f["text"] for f in factors]
    lead = current["rule"]["text"] if current.get("rule") else (texts[0] if texts else "no notable changes")
    headline = f"{TIER_HEADLINE.get(tier, tier)}: {lead}"
    if texts:
        assessment = f"Priority {current['score']:.0f} ({tier.lower()}). Main signals: " + "; ".join(texts) + "."
    else:
        assessment = f"Priority {current['score']:.0f} ({tier.lower()}). Readings are within their usual range."
    checks: list[str] = []
    for f in factors:
        c = CHECKS.get(_check_key(f.get("key", "")))
        if c and c not in checks:
            checks.append(c)
    if not checks:
        checks.append("Routine check on the next round")
    escalation = CODE_STATUS_ESCALATION.get(code, "")
    if tier in ("HIGH", "CRITICAL") and profile.get("escalation"):
        escalation = f"{escalation} Care plan: {profile['escalation']}"
    keys = {_check_key(f.get("key", "")) for f in factors} | {str((current.get("rule") or {}).get("rule") or "")}
    return {
        "headline": headline,
        "assessment": assessment,
        "possible_concerns": [],
        "recommended_checks": checks[:4],
        "escalation": escalation,
        "care_plan_citations": fall_citations(resident) if keys & FALL_KEYS else [],
        "confidence": "low",
        "caveats": "Built from Vigil's scoring factors and the care plan's fixed fields only; not a clinical "
        "assessment. The Snowflake Cortex assessment replaces it when available.",
        "source": "template",
        "model": TEMPLATE_MODEL,
    }


def fall_citations(resident: dict) -> list[dict]:
    """The care plan's mobility and fall-precaution lines, worded exactly as `care_plans.build_care_plan` writes
    them from the same roster fields, so the template can cite them while Cortex is still working."""
    section = "Mobility and fall precautions"
    out = []
    if line := MOBILITY_TEXT.get(str(resident.get("mobility") or "")):
        out.append({"section": section, "quote": line})
    if precautions := (resident.get("profile") or {}).get("fall_precautions"):
        out.insert(0, {"section": section, "quote": f"Fall precautions: {'; '.join(precautions)}."})
    return out


# =========================================================================== Cortex assessment

ASSESS_TIMEOUT_S = 8.0  # the card never waits longer than this for Cortex
CORTEX_TIMEOUT_S = 45.0  # a background call is abandoned after this
SEARCH_TIMEOUT_S = 4.0
MAX_AGE = timedelta(minutes=60)  # an assessment older than this (vs as_of) is not shown; the template is
STALE_AFTER = timedelta(minutes=15)  # top-5 residents are re-assessed after this
RISE_DEBOUNCE_S = 120.0
ALERT_DEBOUNCE_S = 30.0
STALE_RETRY_S = 15 * 60.0
SCAN_INTERVAL_S = 10.0
TOP_N = 5
CONCURRENCY = 2
EVENTS_LOOKBACK = timedelta(hours=12)
TREND_HOURS = 8
CARE_PLAN_CHUNKS = 4
MAX_TOKENS = 3000  # the model's reasoning counts too: at 1500 some cards were cut off mid-JSON
SKIP_EVENTS = ("alert_raised", "alert_acknowledged", "alert_resolved")  # Vigil's own output, not evidence
METRIC_NAME = {"hr": "Heart rate (bpm)", "rr": "Respiratory rate (/min)", "spo2": "SpO2 (%)",
               "skin_temp": "Wrist skin temp (°C)", "motion": "Movement (counts per 15 min, in bed at night)"}  # fmt: skip

ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "assessment": {"type": "string"},
        "possible_concerns": {"type": "array", "items": {"type": "string"}},
        "recommended_checks": {"type": "array", "items": {"type": "string"}},
        "escalation": {"type": "string"},
        "care_plan_citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"section": {"type": "string"}, "quote": {"type": "string"}},
                "required": ["section", "quote"],
            },
        },
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "caveats": {"type": "string"},
    },
    "required": ["headline", "assessment", "possible_concerns", "recommended_checks", "escalation",
                 "care_plan_citations", "confidence", "caveats"],
}  # fmt: skip
RESPONSE_FORMAT = {"type": "json", "schema": ASSESSMENT_SCHEMA}

SYSTEM_PROMPT = """You are Vigil, a decision-support assistant for the night shift of a skilled nursing \
facility. A nurse aide or nurse will read your card on a tablet and decide whom to check. You support their \
judgment; you do not diagnose and you do not replace staff.

Rules:
1. Use ONLY the facts in the CONTEXT. Never invent vital signs, symptoms, notes, findings or history. If \
something was not documented (for example confusion, fever, pain or a fall), do not say it is present; you \
may recommend checking for it.
2. No diagnosis. Say "possible", "could fit", "consider", "check for". Name possible concerns (e.g. "possible \
early infection or delirium") only when the data and care plan support them, and say what supports each one. \
Never write "likely", "probably", "diagnosis", "diagnose", "confirms" or "indicates", and never state a condition \
as present ("has sepsis", "infection likely", "UTI"): write "possible infection", "could fit early infection", \
"check for urinary symptoms". This applies to the headline too.
3. Cite the data behind each claim with numbers from the CONTEXT, e.g. "HR 84 vs her expected 67-79", \
"movement 3x her usual", "drank half her fluids (note 21:30)".
4. Recommend concrete bedside checks staff can do now (mental status vs baseline, temperature, fluids and \
urine, breathing, pain, skin, positioning), most important first.
5. Respect code status and the care plan's escalation preferences. COMFORT/POLST comfort-focused: recommend \
comfort-focused actions and the notification the care plan names; never suggest CPR or hospital transfer \
against it. DNR: no CPR. If the resident takes a beta-blocker, note that heart rate may not rise.
6. care_plan_citations: quote exact sentences or phrases copied verbatim from the CARE PLAN EXCERPTS, with \
the section name shown there. Cite the lines that shaped your advice (history, escalation, code status, fall \
precautions). Never quote text that is not in the excerpts.
7. Be brief; this is read on a tablet in seconds. headline: at most 10 words. assessment: at most 3 \
short sentences (about 60 words). possible_concerns: at most 3, a few words each. recommended_checks: 3-4 \
items, at most 12 words each, most important first. escalation: 1 sentence. care_plan_citations: 1-3. \
Plain words for a nurse aide. Decision-support language ("check", "consider"); never "diagnose", "prevents \
death" or "replaces staff".
8. confidence: how well the data supports your reading (low if inputs are sparse or conflicting). caveats: \
what is missing or uncertain, at most 20 words."""


# --------------------------------------------------------------------------- context


def _hhmm(ts: datetime | str | None, tz: ZoneInfo) -> str:
    if ts is None:
        return "?"
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return ts.astimezone(tz).strftime("%H:%M")


def _num(v: Any, nd: int = 1) -> str:
    return "—" if v is None else f"{float(v):.{nd}f}"


def _news_value(v: Any) -> str:
    if isinstance(v, bool):
        return "new confusion" if v else "alert"
    if isinstance(v, int | float):
        return f"{float(v):.0f}" if float(v).is_integer() or abs(float(v)) >= 50 else f"{float(v):.1f}"
    return str(v)


def describe_event(e: dict, tz: ZoneInfo) -> str | None:
    """One timeline line for the prompt ("HH:MM what"), or None for events that aren't evidence."""
    t, p = e["type"], e.get("payload") or {}
    when = _hhmm(e["ts"], tz)
    if t in SKIP_EVENTS:
        return None
    if t == "note_added":
        return f'{when} note ({p.get("author_role") or "staff"}): "{p.get("text", "")}"'
    if t == "check_logged":
        what = p.get("reason") or (f"round {p['round']}" if p.get("round") else p.get("kind") or "check")
        return f"{when} staff check logged ({what})"
    if t == "intake_logged":
        parts = []
        if p.get("meal"):
            parts.append(f"{p['meal']} {p.get('meal_pct', '?')}% eaten")
        if p.get("fluid_ml") is not None:
            parts.append(f"{p['fluid_ml']} mL fluids")
        return f"{when} intake: {', '.join(parts) or 'logged'}"
    if t == "spot_check":
        bp = f"BP {p['sbp']}/{p.get('dbp', '?')}" if p.get("sbp") else None
        temp = f"temp {p['temp_c']} °C {p.get('method', '')}".strip() if p.get("temp_c") is not None else None
        return f"{when} spot check: {', '.join(x for x in (bp, temp) if x)}"
    if t == "bed_exit":
        return f"{when} out of bed"
    if t == "bed_return":
        return f"{when} back in bed"
    if t == "fall_suspected":
        return f"{when} suspected fall: {p.get('text', 'impact then no movement')}"
    if t in ("device_offline", "device_online"):
        return f"{when} wristband {'offline' if t == 'device_offline' else 'back online'}"
    return f"{when} {t.replace('_', ' ')}"


def _pronoun(resident: dict) -> str:
    return "her" if resident.get("sex") == "F" else "his"


async def build_context(
    conn: psycopg.AsyncConnection, resident: dict, current: dict | None, at: datetime, tz: ZoneInfo
) -> dict:
    """Everything the model may use, as plain data (no Snowflake calls; care-plan chunks are added later)."""
    rid = resident["resident_id"]
    local = at.astimezone(tz)
    block = tod_block(at, tz)

    # Latest two closed buckets judged by Snowflake ANOMALY_DETECTION (known by `at`).
    anomaly = await tiger.insights_range(conn, rid, "anomaly", at - timedelta(hours=1) - BUCKET - REVEAL_DELAY, at)
    buckets = sorted(anomaly, key=lambda r: r["payload"]["bucket"])[-2:]
    vs_expected = []
    for r in buckets:
        b = r["payload"]
        start = datetime.fromisoformat(b["bucket"])
        for m, d in sorted((b.get("metrics") or {}).items()):
            vs_expected.append({
                "bucket": f"{_hhmm(start, tz)}-{_hhmm(start + BUCKET, tz)}", "metric": m, "value": d.get("y"),
                "expected": d.get("forecast"), "range_99": [d.get("lower"), d.get("upper")],
                "outside_range": bool(d.get("severity")), "severity": d.get("severity"),
            })  # fmt: skip

    live_ctx = ev.evaluator.ctx if ev.evaluator is not None else None
    bl = live_ctx.baselines if live_ctx is not None else Baselines(await tiger.load_baseline_rows(conn))
    baselines = bl.block_stats(rid)
    personal = {}  # this time-of-day block, trailing 7 days
    for m in VITAL_METRICS:
        if m in baselines and block in baselines[m]:
            st = baselines[m][block]
            # the baseline job's movement is per 1-min bucket; the trend and Snowflake use 15-min sums
            personal[m] = {k: v * 15 for k, v in st.items()} if m == "motion" else st

    start = (at - timedelta(hours=TREND_HOURS)).replace(minute=0, second=0, microsecond=0)
    rows = await tiger.vitals_series(conn, rid, ["hr", "rr", "spo2", "skin_temp", "motion", "in_bed"], start, at, "15m")
    trend: dict[str, dict] = {}
    for r in rows:
        hour = r["ts"].astimezone(tz).strftime("%H:00")
        h = trend.setdefault(hour, {k: [] for k in ("hr", "rr", "spo2", "skin_temp", "motion", "in_bed")})
        for k, vals in h.items():
            if r.get(k) is not None:
                vals.append(float(r[k]))
    hourly = []
    for hour, h in trend.items():
        row: dict[str, Any] = {"hour": hour}
        for k, vals in h.items():
            if vals:
                row[k] = round(min(vals) if k == "spo2" else sum(vals) / len(vals), 2 if k == "skin_temp" else 1)
        hourly.append(row)

    events = await tiger.fetch_dicts(
        conn,
        """SELECT ts, type, payload FROM events
           WHERE resident_id = %(rid)s AND ts > %(from)s AND ts <= %(at)s ORDER BY ts""",
        {"rid": rid, "from": at - EVENTS_LOOKBACK, "at": at},
    )
    timeline = [line for e in events if (line := describe_event(e, tz))]
    fluids = sum(float((e.get("payload") or {}).get("fluid_ml") or 0) for e in events if e["type"] == "intake_logged")

    # the newest Snowflake FORECAST still driving P_trend, its crossings under the current rules
    copd = "copd" in (resident.get("flags") or ())
    forecast = [annotate_forecast(r["payload"], tz, copd)
                for r in await tiger.insights_range(conn, rid, "forecast", at - FORECAST_FRESH, at)]  # fmt: skip

    cur = current or {}
    comps = cur.get("components") or {}
    news = cur.get("news2") or {}
    return {
        "as_of": at.isoformat(),
        "local_time": f"{local:%H:%M} ({BLOCK_NAMES.get(block, '')}, {local:%a %d %b})",
        "resident": {
            "id": rid, "name": resident.get("name"), "age": resident.get("age"), "sex": resident.get("sex"),
            "unit": resident.get("unit"), "room": resident.get("room"), "diagnoses": resident.get("diagnoses") or [],
            "medications": [f"{m.get('name')} {m.get('dose')}" for m in resident.get("medications") or []],
            "flags": resident.get("flags") or [], "mobility": resident.get("mobility"),
            "cognition": resident.get("cognition"), "morse": resident.get("morse"),
            "code_status": resident.get("code_status"),
        },
        "priority": None if not cur else {
            "score": round(float(cur.get("score") or 0)), "tier": cur.get("tier"),
            "rule": (cur.get("rule") or {}).get("text"),
            "fall_situation": bool(FALL_KEYS & ({str((cur.get("rule") or {}).get("rule") or "")}
                                                | {_check_key(f.get("key", "")) for f in cur.get("factors") or []})),
            "top_factors": [f["text"] for f in cur.get("factors") or []],
            "anomaly_source": comps.get("anom_source"),
            "anomalous_metrics": comps.get("anomalous_metrics") or [],
            "bed_exits_tonight": comps.get("bed_exits"),
            "minutes_since_check": cur.get("minutes_since_check"),
            "latest": cur.get("latest"),
            "note_concern": cur.get("concern"),
        },
        "news2": None if not news else {
            "total": news.get("total"), "spo2_scale": news.get("spo2_scale"), "missing": news.get("missing"),
            "params": {k: f"{_news_value(v['value'])} → {v['points']} pt" for k, v in (news.get("params") or {}).items()},
        },
        "vs_expected": vs_expected,
        "personal_baseline": personal,
        "hourly_trend": hourly,
        "events": timeline,
        "fluids_ml_12h": round(fluids),
        "forecast": forecast[-1:] or None,
        "care_plan": [],
        "tz": tz,
    }  # fmt: skip


SITUATION_WORDS = {
    "hr": "heart rate change", "rr": "breathing rate change", "spo2": "low oxygen", "skin_temp": "feels warm",
    "motion": "restless in bed", "concern": "staff concern in a note", "bed_exits": "getting out of bed",
    "fall_prevention": "getting out of bed at night", "fall": "fall", "unresponsive": "decline in responsiveness",
    "still": "not moving or turning", "hr_extreme": "heart rate change", "rr_extreme": "breathing change",
    "spo2_low": "low oxygen", "overdue": "", "device": "", "device_offline": "", "band_off": "",
}  # fmt: skip
FALL_KEYS = {"bed_exits", "fall_prevention", "fall"}


def care_plan_query(resident: dict, current: dict | None) -> str:
    """Cortex Search query: the situation in words (factor text is mostly numbers, which retrieval ranks
    poorly) + what the card always needs (history, escalation, code status; fall precautions when the
    situation involves getting up or a fall)."""
    cur = current or {}
    keys = [_check_key(f.get("key", "")) for f in cur.get("factors") or []]
    if cur.get("rule"):
        keys.insert(0, str(cur["rule"].get("rule") or ""))
    words = list(dict.fromkeys(w for k in keys if (w := SITUATION_WORDS.get(k, k.replace("_", " ")))))
    needs = "diagnoses and history, escalation preferences, code status"
    if FALL_KEYS & set(keys):
        needs += ", fall precautions"
    return f"{', '.join(words)}: {needs}" if words else f"change in condition: {needs}"


def render_context(ctx: dict) -> str:
    """The context as compact plain text for the user message."""
    r = ctx["resident"]
    lines = [
        f"TIME: {ctx['local_time']} facility time.",
        "",
        "RESIDENT",
        f"{r['name']}, {r['age']}{r['sex']}, {r['unit']} room {r['room']}. Code status: {r['code_status']}.",
        f"Diagnoses: {'; '.join(r['diagnoses']) or 'none listed'}.",
        f"Medications: {'; '.join(r['medications']) or 'none listed'}.",
        f"Flags: {', '.join(r['flags']) or 'none'}. Mobility: {r['mobility']}. Baseline cognition: {r['cognition']}. "
        f"Morse fall score: {r['morse']}.",
        "",
    ]
    p = ctx["priority"]
    if p:
        lines += [
            "VIGIL PRIORITY (deterministic score)",
            f"Score {p['score']} ({p['tier']}). Rule in force: {p['rule'] or 'none'}.",
            f"Top factors: {'; '.join(p['top_factors']) or 'none'}.",
            f"Bed exits tonight: {_num(p['bed_exits_tonight'], 0)}. Minutes since last staff check: "
            f"{_num(p['minutes_since_check'], 0)}.",
            f"Latest readings: HR {_num((p['latest'] or {}).get('hr'), 0)}, RR {_num((p['latest'] or {}).get('rr'))}, "
            f"SpO2 {_num((p['latest'] or {}).get('spo2'), 0)}.",
            "",
        ]  # fmt: skip
    n = ctx["news2"]
    if n:
        params = ", ".join(f"{k} {v}" for k, v in n["params"].items()) or "none"
        lines += [f"NEWS2 (partial, available inputs): total {n['total']}; {params}; SpO2 scale {n['spo2_scale']}; "
                  f"not available: {', '.join(n['missing'] or []) or 'none'}.", ""]  # fmt: skip
    if ctx["vs_expected"]:
        lines.append("SNOWFLAKE ANOMALY DETECTION: latest closed 15-min buckets vs this resident's own expected range")
        for v in ctx["vs_expected"]:
            lo, hi = v["range_99"]
            flag = "OUTSIDE expected range" if v["outside_range"] else "within expected range"
            lines.append(f"- {v['bucket']} {METRIC_NAME.get(v['metric'], v['metric'])}: {_num(v['value'])} "
                         f"(expected {_num(v['expected'])}, 99% range {_num(lo)}-{_num(hi)}) → {flag}")  # fmt: skip
        lines.append("")
    else:
        lines += ["SNOWFLAKE ANOMALY DETECTION: no recent result (not available).", ""]
    if ctx["personal_baseline"]:
        lines.append("PERSONAL BASELINE for this time of day (Tiger, trailing 7 days, p05 / p50 / p95)")
        for m, s in ctx["personal_baseline"].items():
            lines.append(f"- {METRIC_NAME.get(m, m)}: {_num(s['p05'])} / {_num(s['p50'])} / {_num(s['p95'])}")
        lines.append("")
    if ctx["hourly_trend"]:
        lines.append("HOURLY TREND (averages; SpO2 = lowest; motion = wrist movement per 15 min; in_bed = fraction)")
        for h in ctx["hourly_trend"]:
            vals = ", ".join(
                f"{k} {_num(v, 2 if k in ('skin_temp', 'in_bed') else 1)}" for k, v in h.items() if k != "hour"
            )
            lines.append(f"- {h['hour']}: {vals}")
        lines.append("")
    lines.append("EVENTS AND NOTES, last 12 h (oldest first)")
    lines += [f"- {e}" for e in ctx["events"]] or ["- none"]
    lines += [f"Fluid intake logged in the last 12 h: {ctx['fluids_ml_12h']} mL.", ""]
    lines += forecast_lines(ctx["forecast"][-1] if ctx["forecast"] else None, ctx.get("tz"))
    lines += ["", "CARE PLAN EXCERPTS (Cortex Search, this resident only)"]
    if ctx["care_plan"]:
        for c in ctx["care_plan"]:
            lines.append(f"[section: {c['section']}]\n{c['chunk']}")
        if p and p.get("fall_situation"):
            lines += ["", "NOTE: this is a getting-up / fall situation: among your citations, quote the care plan's "
                      "fall-precautions line (the one naming the precautions and assistance needed) verbatim."]  # fmt: skip
    else:
        lines.append("(care plan not available right now)")
    return "\n".join(lines)


def forecast_lines(fc: dict | None, tz: ZoneInfo | None) -> list[str]:
    """The newest Snowflake FORECAST as prompt lines: each metric's last value, the 2-h forecast with its 90 %
    interval, and the first NEWS2 3-point crossing within 4 h."""
    if not fc:
        return ["SNOWFLAKE FORECAST: not available."]
    tz = tz or UTC
    lines = [f"SNOWFLAKE FORECAST (ML.FORECAST, made {_hhmm(fc['as_of'], tz)} from the last {fc.get('train_hours', 12):g} h "
             "of 15-min buckets; 90% interval)"]  # fmt: skip
    for m, d in (fc.get("metrics") or {}).items():
        pts = (d.get("points") or [])[: fc.get("chart_periods", 8)]
        if not pts:
            continue
        end = pts[-1]
        cross = d.get("crossing")
        when = (
            f"; forecast to reach the NEWS2 3-point zone ({'≥' if cross['direction'] == 'up' else '≤'}"
            f"{cross['threshold']:g}) by ~{_hhmm(cross['crosses_at'], tz)}"
            if cross
            else "; no NEWS2 3-point crossing"
        )
        lines.append(f"- {METRIC_NAME.get(m, m)}: last {_num(d.get('last_value'))}, in 2 h {_num(end[1])} "
                     f"(range {_num(end[2])}-{_num(end[3])}){when}")  # fmt: skip
    return lines


def messages_for(ctx: dict) -> list[dict[str, str]]:
    name = ctx["resident"]["name"]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"CONTEXT\n\n{render_context(ctx)}\n\nWrite the assessment card for {name} "
                                    "as JSON matching the schema. Follow the rules exactly."},
    ]  # fmt: skip


# --------------------------------------------------------------------------- validation


class InvalidAssessment(ValueError):
    pass


def _norm(text: str) -> str:
    """Lower-case, markdown bullets/headings and punctuation spacing removed, whitespace collapsed."""
    t = re.sub(r"(^|\s)[-•*#]+\s", " ", text.replace("“", '"').replace("”", '"').replace("’", "'"))
    return " ".join(t.lower().split()).strip(" .\"'")


def _str(v: Any, limit: int) -> str:
    s = " ".join(str(v or "").split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _list(v: Any, n: int, limit: int) -> list[str]:
    items = [_str(x, limit) for x in v] if isinstance(v, list) else []
    return [x for x in items if x][:n]


def validate(obj: dict, chunks: Iterable[dict]) -> dict:
    """Coerce the model's JSON to the card shape; drop citations whose quote isn't in the retrieved chunks."""
    if not isinstance(obj, dict):
        raise InvalidAssessment("not an object")
    missing = [k for k in ASSESSMENT_SCHEMA["required"] if k not in obj]
    if missing:
        raise InvalidAssessment(f"missing {missing}")
    headline, assessment = soften(_str(obj["headline"], 140)), soften(_str(obj["assessment"], 900))
    checks = [soften(c, hedge=False) for c in _list(obj["recommended_checks"], 6, 200)]
    if not headline or not assessment or not checks:
        raise InvalidAssessment("empty headline, assessment or recommended_checks")
    corpus = [(c.get("section") or "", _norm(c.get("chunk") or "")) for c in chunks]
    citations, seen = [], set()
    for c in obj.get("care_plan_citations") or []:
        if not isinstance(c, dict):
            continue
        quote = _str(c.get("quote"), 400).strip('"“” ')
        q = _norm(quote)
        if len(q) < 8 or q in seen:
            continue
        src = next((sec for sec, text in corpus if q in text), None)
        if src is None:
            log.info("assess: dropped a citation not found in the care-plan excerpts: %r", quote[:80])
            continue
        seen.add(q)
        citations.append({"section": _str(c.get("section"), 80) or src, "quote": quote})
    confidence = obj.get("confidence") if obj.get("confidence") in ("low", "medium", "high") else "medium"
    card = {
        "headline": headline,
        "assessment": assessment,
        "possible_concerns": [soften(c) for c in _list(obj["possible_concerns"], 5, 160)],
        "recommended_checks": checks,
        "escalation": soften(_str(obj["escalation"], 400), hedge=False),
        "care_plan_citations": citations[:4],
        "confidence": confidence,
        "caveats": soften(_str(obj["caveats"], 300), hedge=False),
    }
    # Decision-support wording (AGENTS.md rule 9): anything `soften` could not rewrite rejects the card.
    texts = [card["headline"], card["assessment"], card["escalation"], card["caveats"], *card["possible_concerns"],
             *card["recommended_checks"]]  # fmt: skip
    if bad := sorted({v for t in texts for v in violations(t)}):
        raise InvalidAssessment(f"firm diagnostic wording ({', '.join(bad)})")
    return card


def wording_rewrites(raw: dict, card: dict) -> int:
    """How many of the model's texts `validate` had to rewrite into decision-support wording."""
    before = [_str(raw.get(k), 900) for k in ("headline", "assessment", "escalation", "caveats")]
    after = [card[k] for k in ("headline", "assessment", "escalation", "caveats")]
    return sum(1 for a, b in zip(before, after, strict=True) if a != b)


# --------------------------------------------------------------------------- the assessor


@dataclass
class AssessStats:
    phase: str = "starting"  # starting | running | disabled | error
    requests: int = 0
    completed: int = 0
    fallbacks: int = 0  # a caller got the template (timeout or error)
    timeouts: int = 0
    errors: int = 0
    invalid: int = 0
    citations_dropped: int = 0
    wording_rewrites: int = 0  # texts rewritten into decision-support wording (`wording.soften`)
    last_ms: float | None = None
    avg_ms: float | None = None
    last_trigger: str | None = None
    last_error: str | None = None
    last_resident: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _key(rid: int, as_of: datetime | None) -> tuple[int, str | None]:
    return rid, None if as_of is None else as_of.isoformat()


def _sf_ts(ts: datetime) -> str:
    return naive_utc(ts).isoformat(sep=" ", timespec="milliseconds")


class Assessor:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.tz = ZoneInfo(self.settings.facility_tz)
        self.latest: dict[int, dict] = {}  # rid → newest live Cortex assessment payload
        self.inflight: dict[tuple[int, str | None], asyncio.Task] = {}
        self.last_attempt: dict[int, float] = {}  # monotonic start of the newest automatic attempt
        self.seen_tier: dict[int, str] = {}
        self.stats = AssessStats()
        self.triggers = False  # automatic triggers (on alert, scan); set by start() when Snowflake is configured
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._durations: list[float] = []
        self._background: set[asyncio.Task] = set()  # fire-and-forget writes, held so they aren't GC'd

    # ---- cache

    async def load_cache(self, conn: psycopg.AsyncConnection) -> None:
        rows = await tiger.fetch_dicts(
            conn,
            """SELECT DISTINCT ON (resident_id) resident_id, as_of, payload FROM ai_insights
               WHERE kind = 'assessment' AND as_of > now() - %s AND as_of <= now()
               ORDER BY resident_id, as_of DESC""",
            (MAX_AGE,),
        )
        for r in rows:
            self.latest[r["resident_id"]] = r["payload"]

    def headline_for(self, rid: int, at: datetime) -> dict | None:
        """{text, at} for a queue card: the newest live assessment known by `at` and younger than MAX_AGE."""
        a = self.latest.get(rid)
        if not a:
            return None
        made = datetime.fromisoformat(a["as_of"])
        if made > at or at - made > MAX_AGE:
            return None
        return {"text": a["headline"], "at": a["as_of"]}

    async def cached(self, conn: psycopg.AsyncConnection, rid: int, at: datetime, *, live: bool) -> dict | None:
        """Newest Cortex assessment with as_of in (at − MAX_AGE, at]."""
        if live and (a := self.latest.get(rid)):
            made = datetime.fromisoformat(a["as_of"])
            if made <= at and at - made <= MAX_AGE:
                return a
        rows = await tiger.insights_range(conn, rid, "assessment", at - MAX_AGE, at)
        if not rows:
            return None
        if live:  # e.g. the cache wasn't loaded yet: remember it for the queue headline too
            self.latest[rid] = rows[-1]["payload"]
        return rows[-1]["payload"]

    def pending(self, rid: int, as_of: datetime | None = None) -> bool:
        task = self.inflight.get(_key(rid, as_of))
        return task is not None and not task.done()

    # ---- requests

    def request(self, rid: int, as_of: datetime | None, trigger: str, current: dict | None = None) -> asyncio.Task:
        """Start (or join) the assessment for this resident at this time. as_of None = live (now). `current`: the
        score card to explain (replay prepare passes its gated card); default: the live card or a stateless one."""
        key = _key(rid, as_of)
        task = self.inflight.get(key)
        if task is None or task.done():
            self.stats.requests += 1
            task = asyncio.create_task(self._run_one(rid, as_of, trigger, current), name=f"assess-{rid}")
            self.inflight[key] = task
            task.add_done_callback(lambda t, k=key: self._done(k, t))
        return task

    def _done(self, key: tuple[int, str | None], task: asyncio.Task) -> None:
        if self.inflight.get(key) is task:
            self.inflight.pop(key, None)
        if not task.cancelled():
            task.exception()  # already logged by _run_one; triggers and timed-out Refreshes never await it

    async def explain(
        self, rid: int, as_of: datetime | None, trigger: str = "refresh", wait_s: float = ASSESS_TIMEOUT_S
    ) -> tuple[dict | None, str]:
        """Wait up to `wait_s` for a Cortex assessment. Returns (payload, "ok") or (None, reason) — the caller
        then shows the template; a timed-out call keeps running and lands in the cache when done."""
        task = self.request(rid, as_of, trigger)
        try:
            return await asyncio.wait_for(asyncio.shield(task), wait_s), "ok"
        except TimeoutError:
            self.stats.timeouts += 1
            self.stats.fallbacks += 1
            return None, f"Snowflake Cortex is taking longer than {wait_s:.0f} s; showing the template meanwhile"
        except Exception as exc:
            self.stats.fallbacks += 1
            return None, f"Snowflake Cortex unavailable ({type(exc).__name__}); showing the template"

    async def _run_one(self, rid: int, as_of: datetime | None, trigger: str, current: dict | None = None) -> dict:
        async with self._sem:
            try:
                return await asyncio.wait_for(self._assess(rid, as_of, trigger, current), CORTEX_TIMEOUT_S)
            except Exception as exc:
                self.stats.errors += 1
                self.stats.last_error = f"R-{rid}: {' '.join(str(exc).split())[:200] or type(exc).__name__}"
                log.warning("assess: %s", self.stats.last_error)
                raise

    async def _assess(self, rid: int, as_of: datetime | None, trigger: str, current: dict | None = None) -> dict:
        live = as_of is None
        at = datetime.now(UTC).replace(microsecond=0) if live else as_of
        t0 = time.perf_counter()
        async with tiger.pooled() as conn:
            rows = await tiger.fetch_dicts(conn, "SELECT * FROM residents WHERE resident_id = %s", (rid,))
            if not rows:
                raise LookupError(f"no resident {rid}")
            resident = rows[0]
            current = current or await ev.current_for(conn, rid, at, live=live)
            # Cortex Search (Snowflake) runs while the rest of the context is read from Tiger.
            search = asyncio.create_task(self._care_plan(rid, care_plan_query(resident, current)))
            try:
                ctx = await build_context(conn, resident, current, at, self.tz)
            except BaseException:
                search.cancel()
                raise
        ctx["care_plan"] = await search
        rest = rest_client()
        completion = await rest.cortex_complete(
            messages_for(ctx), RESPONSE_FORMAT, max_tokens=MAX_TOKENS, timeout=CORTEX_TIMEOUT_S
        )
        try:
            raw = completion.json()
            card = validate(raw, ctx["care_plan"])
        except (ValueError, InvalidAssessment) as exc:
            self.stats.invalid += 1
            raise InvalidAssessment(f"Cortex output rejected: {exc}") from exc
        self.stats.citations_dropped += len(raw.get("care_plan_citations") or []) - len(card["care_plan_citations"])
        self.stats.wording_rewrites += wording_rewrites(raw, card)
        elapsed = (time.perf_counter() - t0) * 1000
        payload = card | {
            "source": "cortex",
            "model": completion.model,
            "via": completion.via,
            "as_of": at.isoformat(),
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "latency_ms": round(elapsed),
            "cortex_ms": round(completion.latency_ms),
            "trigger": trigger,
            "tier": (current or {}).get("tier"),
            "score": (current or {}).get("score"),
            "care_plan_chunks": len(ctx["care_plan"]),
        }
        async with tiger.pooled() as conn:
            await tiger.upsert_insights(conn, "assessment", [(rid, at, payload)])
            await conn.commit()
        store = asyncio.create_task(self._store_snowflake(rid, at, payload, fallback=False), name=f"assess-store-{rid}")
        self._background.add(store)
        store.add_done_callback(self._background.discard)
        if live:
            self.latest[rid] = payload
        self._record(rid, trigger, elapsed)
        await hub.broadcast({"type": "insight", "kind": "assessment", "resident_id": rid, "as_of": at.isoformat(),
                             "live": live, "headline": payload["headline"], "model": payload["model"]})  # fmt: skip
        log.info("assess: R-%d (%s) %s in %.1f s via %s · %d citations · %s", rid, trigger, payload["model"],
                 elapsed / 1000, completion.via, len(card["care_plan_citations"]), card["headline"])  # fmt: skip
        return payload

    async def _care_plan(self, rid: int, query: str) -> list[dict]:
        """Top care-plan chunks; on failure assess without them rather than not at all."""
        try:
            return await rest_client().search_care_plan(rid, query, CARE_PLAN_CHUNKS, timeout=SEARCH_TIMEOUT_S)
        except Exception as exc:
            log.warning("assess: Cortex Search failed for R-%d: %s", rid, " ".join(str(exc).split())[:160])
            return []

    async def _store_snowflake(self, rid: int, at: datetime, payload: dict, *, fallback: bool) -> None:
        try:
            await rest_client().sql(
                """INSERT INTO CORE.AI_ASSESSMENTS
                     (RESIDENT_ID, AS_OF, MODEL, TRIGGERED_BY, PAYLOAD, FALLBACK, LATENCY_MS, CREATED_AT)
                   SELECT ?, ?::TIMESTAMP_NTZ, ?, ?, PARSE_JSON(?), ?::BOOLEAN, ?, SYSDATE()""",
                [rid, _sf_ts(at), payload.get("model"), payload.get("trigger"), json.dumps(payload),
                 str(fallback).lower(), payload.get("latency_ms") or 0],
                feature="sql_api.store_assessment",
                timeout=20,
            )  # fmt: skip
        except Exception as exc:
            why = " ".join(str(exc).split())[:160] or type(exc).__name__
            log.warning("assess: storing R-%d in CORE.AI_ASSESSMENTS failed: %s", rid, why)

    async def drain(self) -> None:
        """Wait for the fire-and-forget CORE.AI_ASSESSMENTS writes (a script must not close the client under them)."""
        if self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    def _record(self, rid: int, trigger: str, elapsed_ms: float) -> None:
        s = self.stats
        s.completed += 1
        s.last_ms, s.last_trigger, s.last_resident = round(elapsed_ms), trigger, rid
        self._durations = (self._durations + [elapsed_ms])[-50:]
        s.avg_ms = round(sum(self._durations) / len(self._durations))

    # ---- automatic triggers

    def on_alert(self, rid: int, tier: str, *, rule: bool = False) -> asyncio.Task | None:
        """The evaluator just raised a live alert: assess now (the card shows the template meanwhile). A new rule
        (bed exit, fall) re-assesses after 30 s; a score-driven rise keeps the scan's 2-min debounce."""
        from app.state import sim_state

        if not self.triggers or sim_state.mode != "live" or self.pending(rid):
            return None
        now = time.monotonic()
        if now - self.last_attempt.get(rid, -1e9) < (ALERT_DEBOUNCE_S if rule else RISE_DEBOUNCE_S):
            return None
        self.last_attempt[rid] = now
        self.seen_tier[rid] = tier  # the scan must not count this rise again
        return self.request(rid, None, "alert")

    def scan(self, items: list[tuple[int, str, int]], at: datetime) -> list[tuple[int, str]]:
        """(resident_id, tier, rank) of the live queue → residents to assess now, with the trigger name.
        Tier rise into HIGH/CRITICAL (debounced 2 min), or a top-5 non-STABLE resident whose newest assessment
        is older than 15 min (at most one attempt per 15 min, so a failing Snowflake isn't hammered)."""
        now = time.monotonic()
        out = []
        for rid, tier, rank in items:
            prev = self.seen_tier.get(rid)
            since = now - self.last_attempt.get(rid, -1e9)
            # While a call is running or a rise is debounced, keep the tier it was last judged at, so a rise in
            # the meantime is still a rise on a later scan (not silently absorbed).
            if self.pending(rid):
                continue
            rose = prev is not None and TIER_RANK[tier] > TIER_RANK[prev] and tier in ("HIGH", "CRITICAL")
            if rose and since < RISE_DEBOUNCE_S:
                continue
            self.seen_tier[rid] = tier
            if rose:
                out.append((rid, "tier_rise"))
                continue
            if rank <= TOP_N and tier != "STABLE" and since >= STALE_RETRY_S:
                a = self.latest.get(rid)
                if a is None or at - datetime.fromisoformat(a["as_of"]) > STALE_AFTER:
                    out.append((rid, "top5_stale"))
        for rid, _ in out:
            self.last_attempt[rid] = now
        return out

    async def run(self, *, triggers: bool = True) -> None:
        from app.state import sim_state

        while True:
            try:
                async with tiger.pooled() as conn:
                    await self.load_cache(conn)
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.stats.phase, self.stats.last_error = "error", " ".join(str(exc).split())[:200]
                await asyncio.sleep(10)
        if not triggers:  # the cache serves stored assessments; only Refresh asks Cortex
            self.stats.phase = "disabled"
            return
        self.stats.phase = "running"
        while True:
            await asyncio.sleep(SCAN_INTERVAL_S)
            queue = ev.evaluator.latest if ev.evaluator is not None else None
            if queue is None or sim_state.mode != "live" or sim_state.paused:
                continue
            items = [(it.profile.resident_id, it.tier, i) for i, it in enumerate(queue.items, 1)]
            for rid, trigger in self.scan(items, queue.as_of):
                self.request(rid, None, trigger)


assessor: Assessor | None = None


def get_assessor() -> Assessor:
    """The process-wide assessor (created on first use, so the API works even when the loop is off)."""
    global assessor
    if assessor is None:
        assessor = Assessor()
    return assessor


def start() -> asyncio.Task | None:
    settings = get_settings()
    a = get_assessor()
    if not settings.tiger_configured:
        a.stats.phase = "disabled"
        return None
    triggers = settings.assess_enabled and settings.snowflake_configured
    a.triggers = triggers
    return asyncio.create_task(a.run(triggers=triggers), name="assessor")
