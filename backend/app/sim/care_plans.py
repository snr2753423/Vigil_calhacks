"""Synthetic care-plan content per resident (SPEC §9 ④): the text that `scripts/generate_care_plans.py`
renders to PDF and Snowflake indexes for Cortex Search.

Everything comes from the seeded roster (`cast.build_roster`) and each resident's physiology params, so the
care plan agrees with what Tiger holds and what the simulator plays. The scripted cast carry specific,
quotable lines (Grant's bed alarm, Patel's POLST, Alvarez's UTI history) that the AI card cites.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.sim.cast import Resident, resident_params
from app.sim.physiology import PhysioParams

SECTIONS = (
    "Diagnoses and history",
    "Medications and monitoring",
    "Code status and advance directives",
    "Mobility and fall precautions",
    "Baseline cognition and behaviors",
    "Night routine",
    "Escalation preferences",
    "Family contact preferences",
)

CODE_STATUS_TEXT = {
    "FULL": "FULL code: attempt CPR. Hospital transfer OK.",
    "DNR": "DNR: do not attempt CPR. Other treatment and hospital transfer per provider.",
    "DNR-DNI": "DNR-DNI: do not attempt CPR, do not intubate. Other treatment per provider.",
    "COMFORT": "POLST: comfort-focused treatment. No CPR.",
}

DEFAULT_ESCALATION = {
    "FULL": "Notify the charge nurse for any change; the charge nurse calls the provider for acute changes.",
    "DNR": "Notify the charge nurse for any change; the provider decides on treatment or transfer. No CPR.",
    "DNR-DNI": "Notify the charge nurse for any change; the provider decides on treatment. No CPR or intubation.",
    "COMFORT": "For decline: comfort check, then notify the hospice nurse and family. No CPR.",
}

MOBILITY_TEXT = {
    "independent": "Walks independently; standby help as needed.",
    "1-assist": "Assist ×1 for transfers, walking and toileting.",
    "2-assist": "Assist ×2 for transfers; mechanical lift as ordered.",
    "bedbound": "Bedbound. Reposition every 2 hours; pressure-relieving mattress.",
}

COGNITION_TEXT = {
    "intact": "Alert and oriented; makes own decisions and reports symptoms reliably.",
    "mild": "Mild memory loss; oriented to person and place; needs reminders for the call light.",
    "moderate": "Oriented to self only; follows simple one-step requests.",
    "severe": "Nonverbal or few words; depends on staff for all care; watch for nonverbal signs of pain.",
}

FLAG_MONITORING = {
    "beta_blocker": "Takes a beta-blocker: heart rate may not rise with illness, pain or blood loss.",
    "afib": "Atrial fibrillation: irregular pulse is expected; count a full minute.",
    "copd": "COPD: SpO2 target 88-92%; avoid high-flow oxygen unless ordered.",
    "insulin": "Insulin: fingerstick glucose before meals and at bedtime; watch for night-time lows.",
    "opioid": "Opioid as needed: watch for sedation, slow breathing and constipation.",
}

FAMILY_PREFS = (
    "Call for any change in condition, day or night.",
    "Call for significant changes; routine updates at care conferences.",
    "Call between 08:00 and 21:00 unless urgent.",
)

# Specific lines for the scripted cast (SPEC §8): appended to (or replacing) the generated section lines.
CAST_LINES: dict[int, dict[str, list[str]]] = {
    17: {
        "Diagnoses and history": [
            "History of UTIs presenting as confusion: past urinary tract infections showed up as new or worse "
            "confusion and restlessness, often without fever.",
            "Drinks poorly when unwell; encourage fluids and record intake.",
        ],
        "Code status and advance directives": ["FULL code. Hospital transfer OK."],
        "Escalation preferences": [
            "Notify provider for acute mental status change (new or worse confusion, agitation or unusual sleepiness).",
            "With any acute change, check temperature, fluids and urine (color, odor, frequency).",
        ],
    },
    42: {
        "Escalation preferences": [
            "DNR, transfer OK: hospital transfer is OK for treatable problems such as fluid overload.",
            "Notify the provider for new shortness of breath, swelling or weight gain over 1 kg in a day.",
        ],
    },
    101: {
        "Code status and advance directives": [
            "POLST on file: comfort-focused treatment. No CPR. No hospital transfer unless comfort needs cannot "
            "be met here.",
        ],
        "Escalation preferences": [
            "For decline in breathing or responsiveness: comfort check (position, mouth care, pain, breathing "
            "comfort), then notify hospice + daughter Anjali. No CPR.",
        ],
    },
    110: {
        "Mobility and fall precautions": ["Sometimes tries to get up alone at night; answer the bed alarm at once."],
        "Escalation preferences": ["After any fall: do not get him up until checked; neuro checks; notify provider."],
    },
    88: {
        "Medications and monitoring": ["Her usual SpO2 is 88-91% on room air; this is her target, not a concern."],
    },
    91: {
        "Medications and monitoring": ["Her resting heart rate usually runs 85-118 with AFib; this is her normal."],
    },
    120: {
        "Mobility and fall precautions": ["Hip precautions: must call for help to get out of bed; never up alone."],
    },
}


@dataclass(frozen=True)
class CarePlan:
    resident_id: int
    title: str
    identity: list[str]
    sections: list[tuple[str, list[str]]]

    @property
    def filename(self) -> str:
        return filename(self.resident_id)

    def text(self) -> str:
        """Plain text of the whole plan (tests and debugging)."""
        parts = [self.title, *self.identity]
        for heading, lines in self.sections:
            parts += [heading, *lines]
        return "\n".join(parts)


def filename(resident_id: int) -> str:
    """Stage file name; 003_docs.sql reads the resident id back out of it."""
    return f"resident_{resident_id:03d}.pdf"


def _night_routine(r: Resident, p: PhysioParams) -> list[str]:
    lines = []
    if r.profile.get("night_pattern"):
        lines.append(str(r.profile["night_pattern"]))
    elif p.bedbound:
        lines.append("Repositioned every 2 hours overnight; shifts slightly in bed between turns.")
    else:
        trips = round(p.trips_per_night)
        lines.append(
            "Usually sleeps through the night."
            if trips == 0
            else f"Usually wakes about {'once' if trips == 1 else f'{trips} times'} a night for the bathroom."
        )
    lines.append(
        f"In bed around {_clock(20.5 + p.bedtime_offset_min / 60)}; up around {_clock(6.5 + p.wake_offset_min / 60)}."
    )
    return lines


def _clock(hours: float) -> str:
    total = int(round(hours * 4) / 4 * 60) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def build_care_plan(r: Resident, seed: int) -> CarePlan:
    p = resident_params(seed, r)
    prof = r.profile
    contact = r.emergency_contact or {}
    flags = r.flags

    history = [f"Diagnoses: {'; '.join(r.diagnoses)}."]
    if prof.get("history"):
        history.append(str(prof["history"]))

    meds = [f"{m.name} {m.dose}" for m in r.medications] or ["No scheduled medications."]
    monitoring = [FLAG_MONITORING[f] for f in flags if f in FLAG_MONITORING]
    if prof.get("monitoring"):
        monitoring.append(str(prof["monitoring"]))

    code = [CODE_STATUS_TEXT[r.code_status]]
    if prof.get("polst") and r.code_status != "COMFORT":
        code.append(f"POLST: {prof['polst']}")
    elif prof.get("polst"):
        code = [f"POLST: {prof['polst']}"]
    if prof.get("hospice"):
        code.append("Enrolled in hospice.")

    precautions = list(prof.get("fall_precautions") or [])
    mobility = [MOBILITY_TEXT[r.mobility], f"Morse Fall Scale: {r.morse} ({_morse_band(r.morse)} risk)."]
    if precautions:
        mobility.append(f"Fall precautions: {'; '.join(precautions)}.")
    else:
        mobility.append("Fall precautions: standard (call light within reach, clear path to bathroom).")

    cognition = [str(prof.get("baseline_cognition") or COGNITION_TEXT[r.cognition])]
    if prof.get("behaviors"):
        cognition.append(str(prof["behaviors"]))
    if r.cognition in ("moderate", "severe"):
        cognition.append("May not report pain or symptoms; watch for changes in behavior, sleep or eating.")

    escalation = [str(prof.get("escalation") or DEFAULT_ESCALATION[r.code_status])]

    family = []
    if contact:
        family.append(f"Primary contact: {contact.get('name')} ({contact.get('relation')}), {contact.get('phone')}.")
    family.append(str(prof.get("family_contact") or FAMILY_PREFS[r.resident_id % len(FAMILY_PREFS)]))

    sections = {
        "Diagnoses and history": history,
        "Medications and monitoring": [f"Medications: {'; '.join(meds)}.", *monitoring],
        "Code status and advance directives": code,
        "Mobility and fall precautions": mobility,
        "Baseline cognition and behaviors": cognition,
        "Night routine": _night_routine(r, p),
        "Escalation preferences": escalation,
        "Family contact preferences": family,
    }
    for heading, extra in CAST_LINES.get(r.resident_id, {}).items():
        if heading == "Code status and advance directives":
            sections[heading] = extra + [x for x in sections[heading] if not x.startswith(("FULL", "POLST"))]
        elif heading == "Escalation preferences":  # the cast's own wording replaces the profile summary
            sections[heading] = extra
        elif heading == "Diagnoses and history":
            sections[heading] = sections[heading][:1] + extra
        else:
            sections[heading] = sections[heading] + extra

    identity = [
        f"Resident: {r.name} (R-{r.resident_id}) · {r.age} · {'female' if r.sex == 'F' else 'male'}",
        f"Unit: {r.unit} · Room {r.room} · Code status: {r.code_status}",
        "Synthetic demo record. Not a real person.",
    ]
    return CarePlan(r.resident_id, f"Care Plan: {r.name}", identity, [(h, sections[h]) for h in SECTIONS])


def _morse_band(morse: int) -> str:
    return "high" if morse >= 45 else "moderate" if morse >= 25 else "low"
