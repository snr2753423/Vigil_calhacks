"""Cortex AI assessment with care-plan citations + deterministic fallback (SPEC §9 ⑤).

The Cortex call arrives in Phase 6. `template_assessment` is the fallback the card shows whenever no AI
assessment exists or Snowflake is slow/down (AGENTS.md rule 5): built only from the deterministic factors,
the code status and the resident's care-plan profile, in the same JSON shape as the Cortex output.
"""

from __future__ import annotations

TEMPLATE_MODEL = "deterministic template"

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
    return {
        "headline": headline,
        "assessment": assessment,
        "possible_concerns": [],
        "recommended_checks": checks[:4],
        "escalation": escalation,
        "care_plan_citations": [],
        "confidence": "low",
        "caveats": "Built from Vigil's scoring factors only; not a clinical assessment. "
        "The Snowflake Cortex assessment with care-plan citations replaces it when available.",
        "source": "template",
        "model": TEMPLATE_MODEL,
    }
