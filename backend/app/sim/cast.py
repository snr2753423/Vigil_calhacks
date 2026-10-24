"""Resident roster (120 synthetic residents) and the scripted "last night" cast (SPEC §2, §8).

The roster is generated from the seed, with the eight cast residents pinned exactly as SPEC §8
describes. `scripted_night()` returns the Scenario objects that play last night's story; the same
objects drive the seed and the live loop, so data stays consistent across both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from zoneinfo import ZoneInfo

import numpy as np

from app.sim import facility as fac
from app.sim.physiology import (
    FIDGETS_PER_NIGHT,
    DayPlan,
    PhysioParams,
    ResidentSim,
    Scenario,
    Signals,
    SimEvent,
    Trip,
    assist_check,
    make_params,
)

# --------------------------------------------------------------------------- roster


@dataclass(frozen=True)
class Medication:
    name: str
    dose: str
    flags: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"name": self.name, "dose": self.dose, "flags": list(self.flags)}


@dataclass(frozen=True)
class Resident:
    resident_id: int
    name: str
    age: int
    sex: str
    unit: str
    room: str
    room_index: int
    diagnoses: tuple[str, ...]
    medications: tuple[Medication, ...]
    morse: int
    mobility: str
    cognition: str
    code_status: str
    emergency_contact: dict
    profile: dict = field(default_factory=dict)
    is_live: bool = False

    @property
    def flags(self) -> tuple[str, ...]:
        flags = {f for m in self.medications for f in m.flags}
        dx = " ".join(self.diagnoses).lower()
        if "atrial fibrillation" in dx:
            flags.add("afib")
        if "copd" in dx:
            flags.add("copd")
        return tuple(sorted(flags))


M = Medication
BETA = ("beta_blocker",)
OPIOID = ("opioid",)
INSULIN = ("insulin",)

# Diagnosis → medication options (one is picked per resident).
_MEDS: dict[str, tuple[tuple[Medication, ...], ...]] = {
    "Hypertension": (
        (M("Amlodipine", "5 mg daily"),),
        (M("Lisinopril", "10 mg daily"),),
        (M("Losartan", "50 mg daily"),),
        (M("Metoprolol tartrate", "25 mg twice daily", BETA),),
    ),
    "Heart failure": (
        (M("Metoprolol succinate", "50 mg daily", BETA), M("Furosemide", "40 mg daily")),
        (M("Carvedilol", "6.25 mg twice daily", BETA), M("Furosemide", "20 mg daily")),
    ),
    "Atrial fibrillation": (
        (M("Apixaban", "5 mg twice daily"), M("Diltiazem ER", "180 mg daily")),
        (M("Apixaban", "2.5 mg twice daily"), M("Metoprolol tartrate", "25 mg twice daily", BETA)),
    ),
    "COPD": ((M("Tiotropium inhaler", "18 mcg daily"), M("Albuterol inhaler", "2 puffs every 4 h as needed")),),
    "Type 2 diabetes": (
        (M("Metformin", "500 mg twice daily"),),
        (M("Metformin", "500 mg twice daily"),),
        (M("Insulin glargine", "14 units at bedtime", INSULIN),),
    ),
    "Osteoarthritis": (
        (M("Acetaminophen", "650 mg three times daily"),),
        (M("Acetaminophen", "650 mg as needed"),),
        (M("Tramadol", "50 mg every 6 h as needed", OPIOID),),
    ),
    "Hypothyroidism": ((M("Levothyroxine", "50 mcg daily"),),),
    "Parkinson's disease": ((M("Carbidopa/levodopa", "25/100 mg three times daily"),),),
    "Depression": ((M("Sertraline", "50 mg daily"),), (M("Mirtazapine", "15 mg at bedtime"),)),
    "Hyperlipidemia": ((M("Atorvastatin", "20 mg daily"),),),
    "Osteoporosis": ((M("Alendronate", "70 mg weekly"), M("Calcium + vitamin D", "daily")),),
    "Glaucoma": ((M("Latanoprost eye drops", "1 drop nightly"),),),
    "Benign prostatic hyperplasia": ((M("Tamsulosin", "0.4 mg at bedtime"),),),
    "Chronic kidney disease stage 3": ((),),
    "History of stroke": ((M("Clopidogrel", "75 mg daily"),),),
    "Peripheral neuropathy": ((M("Gabapentin", "100 mg three times daily"),),),
    "Macular degeneration": ((),),
    "Anemia of chronic disease": ((),),
    "dementia": ((M("Donepezil", "10 mg at bedtime"),), (M("Memantine", "10 mg twice daily"),)),
    "post-op": (
        (M("Oxycodone", "5 mg every 6 h as needed", OPIOID), M("Enoxaparin", "40 mg SC daily")),
        (M("Acetaminophen", "650 mg every 6 h"), M("Enoxaparin", "40 mg SC daily")),
    ),
    "hospice": ((M("Morphine oral solution", "5 mg every 4 h as needed", OPIOID), M("Lorazepam", "0.5 mg as needed")),),
}

_COMORBIDITIES = (
    "Hypertension",
    "Hypertension",
    "Type 2 diabetes",
    "Chronic kidney disease stage 3",
    "Osteoarthritis",
    "Hypothyroidism",
    "Heart failure",
    "COPD",
    "Atrial fibrillation",
    "Parkinson's disease",
    "History of stroke",
    "Depression",
    "Osteoporosis",
    "Hyperlipidemia",
    "Glaucoma",
    "Macular degeneration",
    "Peripheral neuropathy",
    "Anemia of chronic disease",
)
_REHAB_PRIMARY = (
    ("S/p left hip fracture repair (ORIF)", "post-op"),
    ("S/p right total knee replacement", "post-op"),
    ("S/p total hip replacement", "post-op"),
    ("Pneumonia, recovering", None),
    ("Heart failure exacerbation, recovering", "Heart failure"),
    ("Ischemic stroke, rehabilitation", "History of stroke"),
    ("Sepsis, recovering", None),
    ("Deconditioning after hospitalization", None),
    ("Lumbar compression fracture", "post-op"),
)
_DEMENTIA = ("Alzheimer's dementia", "Vascular dementia", "Lewy body dementia", "Mixed dementia")

_UNIT_MIX = {
    # mobility, cognition probabilities
    "East": (
        {"independent": 0.25, "1-assist": 0.35, "2-assist": 0.25, "bedbound": 0.15},
        {"intact": 0.45, "mild": 0.35, "moderate": 0.20},
    ),
    "West": (
        {"independent": 0.15, "1-assist": 0.55, "2-assist": 0.25, "bedbound": 0.05},
        {"intact": 0.65, "mild": 0.30, "moderate": 0.05},
    ),
    "Memory Care": (
        {"independent": 0.30, "1-assist": 0.45, "2-assist": 0.20, "bedbound": 0.05},
        {"mild": 0.10, "moderate": 0.55, "severe": 0.35},
    ),
}

_F_NAMES = (
    "Dorothy", "Margaret", "Helen", "Ruth", "Betty", "Shirley", "Barbara", "Patricia", "Joan", "Jean", "Frances",
    "Gloria", "Evelyn", "Mildred", "Lorraine", "Carmen", "Lucia", "Mei", "Yoko", "Priya", "Fatima", "Esther",
    "Gladys", "Irene", "Marjorie", "Bernice", "Lillian", "Alma", "Consuelo", "Annie", "Hattie", "Ida", "Josephine",
    "Vera", "Nadia", "Sofia", "Teresa", "Rita", "Agnes", "Beatrice", "Clara", "Edna", "Florence", "Hazel", "June",
    "Loretta", "Marian", "Norma", "Olive", "Pearl", "Ramona", "Sylvia", "Thelma", "Viola", "Wanda", "Yolanda",
)  # fmt: skip
_M_NAMES = (
    "Robert", "William", "Richard", "Charles", "George", "Frank", "Harold", "Walter", "Raymond", "Eugene", "Ernest",
    "Herbert", "Leonard", "Albert", "Clarence", "Howard", "Ralph", "Samuel", "Joseph", "Manuel", "Hector", "Hiroshi",
    "Kenji", "Wei", "Rajesh", "Isaac", "Louis", "Melvin", "Norman", "Stanley", "Vernon", "Willie", "Earl", "Floyd",
    "Gilbert", "Lloyd", "Marvin", "Otis", "Roy", "Francisco",
)  # fmt: skip
_LAST_NAMES = (
    "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez",
    "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Thompson", "White",
    "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King", "Scott",
    "Torres", "Nguyen", "Hill", "Flores", "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell",
    "Mitchell", "Carter", "Roberts", "Chen", "Wang", "Nakamura", "Tanaka", "Kowalski", "Novak", "Schmidt", "Murphy",
    "O'Brien", "Sullivan", "Shapiro", "Goldberg", "Singh", "Sharma", "Castillo", "Morales", "Brooks", "Foster",
    "Hughes", "Price", "Bennett", "Wood", "Ross", "Henderson", "Coleman", "Jenkins", "Perry", "Powell", "Long",
    "Patterson", "Washington", "Butler", "Simmons", "Bryant", "Russell", "Diaz", "Hayes", "Myers", "Ford", "Graham",
    "Sullivan-Hart", "Wallace", "West", "Cole", "Jordan", "Reynolds", "Hamilton", "Fisher", "Ellis", "Harrison",
    "Gibson", "McDonald", "Cruz", "Marshall", "Ortega", "Gomez", "Murray", "Freeman", "Wells", "Webb", "Simpson",
    "Stevens", "Tucker", "Porter", "Hunter", "Hicks", "Crawford", "Henry", "Boyd", "Mason", "Kennedy", "Warren",
)  # fmt: skip
_RELATIONS = ("daughter", "son", "spouse", "niece", "nephew", "daughter", "son")

# ---- the scripted cast (SPEC §8) -------------------------------------------------------------

CAST_IDS = (17, 42, 101, 110, 88, 91, 33, 120)

_CAST: dict[int, dict] = {
    17: dict(
        name="Rosa Alvarez", age=87, sex="F", unit="Memory Care",
        diagnoses=("Alzheimer's dementia, moderate", "Hypertension", "Recurrent urinary tract infections",
                   "Osteoarthritis"),
        medications=(M("Donepezil", "10 mg at bedtime"), M("Amlodipine", "5 mg daily"),
                     M("Acetaminophen", "650 mg every 6 h as needed")),
        morse=55, mobility="1-assist", cognition="moderate", code_status="FULL",
        contact={"name": "Marisol Alvarez", "relation": "daughter", "phone": "(415) 555-0117"},
        profile={
            "baseline_cognition": "Oriented to self only; follows simple one-step requests; pleasant and social.",
            "night_pattern": "Usually wakes about once a night for the bathroom; needs reminders to use the call light.",
            "history": "Past UTIs have shown up as new or worse confusion and restlessness rather than fever.",
            "fall_precautions": ["bed alarm", "low bed", "non-skid socks", "assist ×1 to bathroom"],
            "escalation": "Notify the provider for any acute change in mental status. Full code; hospital transfer OK.",
            "family_contact": "Daughter Marisol wants a call for any change, day or night.",
        },
        physio=dict(hr_base=68.0, hr_sleep_drop=0.10, hr_activity_gain=20.0, hr_noise_sd=1.8, rr_base=16.0,
                    spo2_base=96.0, skin_base=33.6, trips_per_night=0.8, restless=1.2),
    ),
    42: dict(
        name="Henry Lee", age=91, sex="M", unit="East",
        diagnoses=("Heart failure with reduced ejection fraction", "Hypertension", "Chronic kidney disease stage 3",
                   "Gout"),
        medications=(M("Metoprolol succinate", "50 mg daily", BETA), M("Furosemide", "40 mg daily"),
                     M("Lisinopril", "5 mg daily"), M("Allopurinol", "100 mg daily")),
        morse=45, mobility="1-assist", cognition="intact", code_status="DNR",
        contact={"name": "Grace Lee", "relation": "daughter", "phone": "(415) 555-0142"},
        profile={
            "baseline_cognition": "Alert and oriented; reports symptoms reliably.",
            "night_pattern": "Sleeps with the head of the bed raised; up once a night to urinate (diuretic).",
            "monitoring": "Daily weights; 1.5 L fluid restriction; report weight gain over 1 kg in a day.",
            "fall_precautions": ["call light within reach", "walker at bedside"],
            "escalation": "DNR. Hospital transfer is OK for treatable problems (e.g., fluid overload). "
                          "Notify the provider for new shortness of breath or swelling.",
            "family_contact": "Call daughter Grace for transfers or significant changes.",
        },
        physio=dict(hr_base=58.0, hr_sleep_drop=0.08, hr_activity_gain=10.0, hr_noise_sd=1.4, rr_base=17.0,
                    spo2_base=95.0, trips_per_night=1.0),
    ),
    101: dict(
        name="Arthur Patel", age=94, sex="M", unit="East",
        diagnoses=("Metastatic prostate cancer (hospice)", "Chronic kidney disease stage 4", "Anemia of chronic disease"),
        medications=(M("Morphine oral solution", "5 mg every 4 h as needed", OPIOID), M("Lorazepam", "0.5 mg as needed"),
                     M("Senna", "8.6 mg at bedtime")),
        morse=35, mobility="bedbound", cognition="moderate", code_status="COMFORT",
        contact={"name": "Anjali Patel", "relation": "daughter", "phone": "(415) 555-0101"},
        profile={
            "hospice": True,
            "polst": "Comfort-focused treatment. No CPR. No hospital transfer unless comfort needs cannot be met here.",
            "baseline_cognition": "Drowsy, rouses to voice, follows simple requests.",
            "night_pattern": "Repositioned every 2 hours; shifts himself in bed between turns.",
            "escalation": "For decline: comfort check, then notify the hospice nurse and daughter Anjali. No CPR.",
            "family_contact": "Daughter Anjali wants a call at any hour for changes in breathing or responsiveness.",
        },
        physio=dict(hr_base=68.0, hr_sleep_drop=0.06, hr_activity_gain=8.0, hr_noise_sd=1.5, rr_base=13.0,
                    spo2_base=94.5, turn_cadence_min=24.0, nap_prob=0.9),
    ),
    110: dict(
        name="James Ortiz", age=82, sex="M", unit="West",
        diagnoses=("Parkinson's disease", "History of falls", "Orthostatic hypotension", "Benign prostatic hyperplasia"),
        medications=(M("Carbidopa/levodopa", "25/100 mg three times daily"), M("Tamsulosin", "0.4 mg at bedtime"),
                     M("Midodrine", "5 mg three times daily")),
        morse=75, mobility="1-assist", cognition="mild", code_status="FULL",
        contact={"name": "Miguel Ortiz", "relation": "son", "phone": "(415) 555-0110"},
        profile={
            "night_pattern": "Up about twice a night to urinate; sometimes tries to go alone.",
            "fall_precautions": ["bed alarm", "low bed", "floor mat", "assist ×1 to bathroom", "call light within reach"],
            "escalation": "After any fall: neuro checks, notify provider and son. Full code.",
            "family_contact": "Son Miguel wants a call after any fall.",
        },
        physio=dict(trips_per_night=2.0, assist_prob=0.8),
    ),
    88: dict(
        name="Margaret Kim", age=79, sex="F", unit="East",
        diagnoses=("COPD", "Hypertension", "Osteoporosis"),
        medications=(M("Tiotropium inhaler", "18 mcg daily"), M("Albuterol inhaler", "2 puffs every 4 h as needed"),
                     M("Losartan", "50 mg daily")),
        morse=35, mobility="independent", cognition="intact", code_status="DNR-DNI",
        contact={"name": "Daniel Kim", "relation": "son", "phone": "(415) 555-0188"},
        profile={
            "monitoring": "Pulmonology target SpO2 88–92%; her usual range is 88–91% on room air.",
            "escalation": "DNR-DNI. Notify the provider for increased work of breathing.",
        },
        physio=dict(spo2_base=89.5, spo2_lo=88.0, spo2_hi=91.0),
    ),
    91: dict(
        name="Elena Rossi", age=84, sex="F", unit="West",
        diagnoses=("Atrial fibrillation, rate-controlled", "Hypertension", "S/p right total knee replacement"),
        medications=(M("Apixaban", "5 mg twice daily"), M("Diltiazem ER", "180 mg daily"),
                     M("Acetaminophen", "650 mg every 6 h")),
        morse=40, mobility="independent", cognition="intact", code_status="FULL",
        contact={"name": "Paolo Rossi", "relation": "son", "phone": "(415) 555-0191"},
        profile={"monitoring": "Chronic AFib: resting heart rate usually 85–118."},
        physio=dict(hr_base=100.0, hr_min=85.0, hr_max=118.0),
    ),
    33: dict(
        name="Doris Wright", age=80, sex="F", unit="East",
        diagnoses=("Type 2 diabetes", "Osteoarthritis", "Hypothyroidism"),
        medications=(M("Insulin glargine", "14 units at bedtime", INSULIN), M("Levothyroxine", "75 mcg daily"),
                     M("Acetaminophen", "650 mg as needed")),
        morse=25, mobility="independent", cognition="intact", code_status="FULL",
        contact={"name": "Carol Wright", "relation": "daughter", "phone": "(415) 555-0133"},
        profile={"monitoring": "Fingerstick glucose before meals and at bedtime."},
        physio=dict(),
    ),
    120: dict(
        name="Thomas Grant", age=78, sex="M", unit="West",
        diagnoses=("S/p left hip fracture repair (ORIF)", "Hypertension", "Benign prostatic hyperplasia"),
        medications=(M("Oxycodone", "5 mg every 6 h as needed", OPIOID), M("Acetaminophen", "650 mg every 6 h"),
                     M("Enoxaparin", "40 mg SC daily"), M("Amlodipine", "5 mg daily")),
        morse=70, mobility="1-assist", cognition="intact", code_status="FULL",
        contact={"name": "Susan Grant", "relation": "spouse", "phone": "(415) 555-0120"},
        profile={
            "fall_precautions": ["bed alarm", "assist ×1 to bathroom", "walker at bedside", "non-skid socks"],
            "night_pattern": "Up once or twice a night to urinate; must call for help (hip precautions).",
            "escalation": "Full code. Notify provider for falls, new hip pain, or calf swelling.",
        },
        physio=dict(hr_base=72.0, trips_per_night=1.2),
        is_live=True,
    ),
}  # fmt: skip


def _weighted(rng: np.random.Generator, options: dict[str, float]) -> str:
    keys = list(options)
    probs = np.array([options[k] for k in keys], dtype=float)
    return keys[int(rng.choice(len(keys), p=probs / probs.sum()))]


def _assign_units() -> dict[int, str]:
    """Cast residents sit on their spec'd unit; everyone else fills East, then West, then Memory Care."""
    units = {rid: c["unit"] for rid, c in _CAST.items()}
    counts = {u: list(units.values()).count(u) for u in fac.UNITS}
    for rid in fac.RESIDENT_IDS:
        if rid in units:
            continue
        unit = next(u for u in fac.UNITS if counts[u] < fac.RESIDENTS_PER_UNIT)
        units[rid] = unit
        counts[unit] += 1
    return units


def _morse(rng, mobility: str, cognition: str, n_dx: int, fell_before: bool) -> int:
    score = 25 if fell_before else 0
    score += 15 if n_dx >= 2 else 0
    aid, gait = {
        "independent": ((0, 15), (0, 10)),
        "1-assist": ((15, 15), (10, 20)),
        "2-assist": ((15, 30), (20, 20)),
        "bedbound": ((0, 0), (0, 0)),
    }[mobility]
    score += int(rng.choice(aid)) + int(rng.choice(gait))
    if cognition in ("moderate", "severe") or (cognition == "mild" and rng.random() < 0.4):
        score += 15
    return score


def _random_resident(rid: int, unit: str, room_index: int, rng: np.random.Generator, used: set[str]) -> Resident:
    sex = "F" if rng.random() < 0.66 else "M"
    while True:
        first = str(rng.choice(_F_NAMES if sex == "F" else _M_NAMES))
        last = str(rng.choice(_LAST_NAMES))
        if f"{first} {last}" not in used:
            break
    age = int(np.clip(round(rng.normal(84, 7)), 66, 101))
    mob_mix, cog_mix = _UNIT_MIX[unit]
    mobility = _weighted(rng, mob_mix)
    cognition = _weighted(rng, cog_mix)

    diagnoses: list[str] = []
    med_keys: list[str] = []
    if unit == "West":
        primary, med_key = _REHAB_PRIMARY[int(rng.integers(len(_REHAB_PRIMARY)))]
        diagnoses.append(primary)
        if med_key:
            med_keys.append(med_key)
    elif unit == "Memory Care":
        stage = {"mild": "mild", "moderate": "moderate", "severe": "severe"}[cognition]
        diagnoses.append(f"{_DEMENTIA[int(rng.integers(len(_DEMENTIA)))]}, {stage}")
        med_keys.append("dementia")
    hospice = unit == "East" and mobility == "bedbound" and rng.random() < 0.3
    if hospice:
        diagnoses.append(str(rng.choice(("End-stage heart failure (hospice)", "Metastatic lung cancer (hospice)"))))
        med_keys.append("hospice")
    pool = [d for d in _COMORBIDITIES if not (d == "Benign prostatic hyperplasia" and sex == "F")]
    if sex == "M":
        pool.append("Benign prostatic hyperplasia")
    for dx in rng.choice(pool, size=int(rng.integers(1, 4)), replace=False):
        if dx not in diagnoses:
            diagnoses.append(str(dx))
            med_keys.append(str(dx))

    meds: list[Medication] = []
    for key in med_keys:
        options = _MEDS.get(key, ((),))
        for med in options[int(rng.integers(len(options)))]:
            if med.name not in {m.name for m in meds}:
                meds.append(med)

    code_status = "COMFORT" if hospice else _weighted(rng, {"FULL": 0.47, "DNR": 0.37, "DNR-DNI": 0.16})
    fell = rng.random() < (0.5 if unit == "West" else 0.3)
    morse = _morse(rng, mobility, cognition, len(diagnoses), fell)
    relation = str(rng.choice(_RELATIONS))
    contact_first = str(rng.choice(_F_NAMES if rng.random() < 0.55 else _M_NAMES))
    contact_last = last if relation != "niece" and relation != "nephew" else str(rng.choice(_LAST_NAMES))
    contact = {
        "name": f"{contact_first} {contact_last}",
        "relation": relation,
        "phone": f"(415) 555-01{int(rng.integers(0, 100)):02d}",
    }
    precautions = []
    if morse >= 45 and mobility != "bedbound":
        precautions = ["bed alarm", "low bed", "non-skid socks", "call light within reach"]
    if mobility in ("1-assist", "2-assist"):
        precautions.append(f"assist ×{1 if mobility == '1-assist' else 2} with transfers and toileting")
    profile = {"fall_precautions": precautions}
    if unit == "Memory Care" and rng.random() < 0.4:
        profile["behaviors"] = "Sundowning in the early evening; responds to redirection and a snack."
    if hospice:
        profile["hospice"] = True
        profile["polst"] = "Comfort-focused treatment. No CPR."
    name = f"{first} {last}"
    used.add(name)
    return Resident(
        resident_id=rid,
        name=name,
        age=age,
        sex=sex,
        unit=unit,
        room=str(fac.UNIT_ROOM_BASE[unit] + room_index),
        room_index=room_index,
        diagnoses=tuple(diagnoses),
        medications=tuple(meds),
        morse=morse,
        mobility=mobility,
        cognition=cognition,
        code_status=code_status,
        emergency_contact=contact,
        profile=profile,
    )


def build_roster(seed: int) -> list[Resident]:
    rng = np.random.default_rng([seed, 11])
    units = _assign_units()
    room_index: dict[int, int] = {}
    for unit in fac.UNITS:
        for i, rid in enumerate(sorted(r for r, u in units.items() if u == unit)):
            room_index[rid] = i
    used = {c["name"] for c in _CAST.values()}
    roster = []
    for rid in fac.RESIDENT_IDS:
        unit, idx = units[rid], room_index[rid]
        if rid in _CAST:
            c = _CAST[rid]
            roster.append(
                Resident(
                    resident_id=rid,
                    name=c["name"],
                    age=c["age"],
                    sex=c["sex"],
                    unit=unit,
                    room=str(fac.UNIT_ROOM_BASE[unit] + idx),
                    room_index=idx,
                    diagnoses=c["diagnoses"],
                    medications=c["medications"],
                    morse=c["morse"],
                    mobility=c["mobility"],
                    cognition=c["cognition"],
                    code_status=c["code_status"],
                    emergency_contact=c["contact"],
                    profile=c["profile"],
                    is_live=c.get("is_live", False),
                )
            )
        else:
            roster.append(_random_resident(rid, unit, idx, rng, used))
    return roster


def resident_params(seed: int, r: Resident, live_resting_hr: float | None = None) -> PhysioParams:
    overrides = dict(_CAST[r.resident_id]["physio"]) if r.resident_id in _CAST else {}
    if r.is_live and live_resting_hr:
        overrides["hr_base"] = float(live_resting_hr)
    return make_params(
        seed,
        r.resident_id,
        unit=r.unit,
        room_index=r.room_index,
        mobility=r.mobility,
        cognition=r.cognition,
        morse=r.morse,
        flags=frozenset(r.flags),
        **overrides,
    )


# --------------------------------------------------------------------------- scripted "last night"


def _knots(t: np.ndarray, pts: list[tuple[float, float]]) -> np.ndarray:
    xs, ys = zip(*pts, strict=True)
    return np.interp(t, xs, ys)


class _NightScript(Scenario):
    """A cast script anchored to the scripted night. Hours are relative to local midnight of the night's
    evening date (22.0 = 22:00 that evening, 26.25 = 02:15 the next morning)."""

    def __init__(self, resident_id: int, night: date, tz: ZoneInfo, start_h: float, end_h: float = 36.0):
        self.resident_id = resident_id
        self.night = night
        self.tz = tz
        self.start = self.at(start_h)
        self.end = self.at(end_h)

    def at(self, hours: float) -> float:
        return fac.local_epoch(self.night, hours, self.tz)

    def pts(self, pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [(self.at(h), v) for h, v in pts]

    def is_night_plan(self, plan: DayPlan) -> bool:
        return plan.day == self.night

    def event(self, hours: float, type_: str, staff_id: int | None, payload: dict) -> SimEvent:
        return SimEvent(self.at(hours), self.resident_id, type_, staff_id, payload)

    @staticmethod
    def drop_routine_notes(plan: DayPlan) -> None:
        plan.events[:] = [e for e in plan.events if not (e.type == "note_added" and e.payload.get("routine"))]

    def replace_trips(self, plan: DayPlan, params: PhysioParams, trips: list[Trip]) -> None:
        """Swap the night's bathroom trips, keeping the call-light checks in step with them."""
        replace_trips(plan, params, trips, self.at(24))


def replace_trips(plan: DayPlan, params: PhysioParams, trips: list[Trip], midnight: float) -> None:
    plan.trips = trips
    plan.events[:] = [e for e in plan.events if not (e.type == "check_logged" and e.payload.get("reason") == BATHROOM)]
    plan.events += [assist_check(params.resident_id, params.unit, t.start - 120, midnight) for t in trips if t.assisted]


BATHROOM = "assisted to bathroom"


FIXED_ALARM_CLEAR_UNTIL_H = 27.0 + 50 / 60  # 03:50: no fixed-threshold alarm for Alvarez before this


class AlvarezDrift(_NightScript):
    """Rosa Alvarez (17): slow UTI → early-sepsis drift from the previous afternoon.

    HR 68 → 88 by 02:14 (still under 100), RR 16 → 22, wrist skin temp +0.8 °C. She does not get up alone:
    the drift shows as restlessness in bed (more frequent turning and fidgeting, growing through the night)
    and two call-light toileting trips with the CNA (a check logged 2 min before each), so the fall-prevention
    rule never fires and the deep tier (Snowflake anomalies vs her own normal) has to find her. The 21:30
    CNA note reads as low concern; the confusion is only discovered at 02:20, when an aide goes to check on
    her because Vigil put her at the top of the queue. No fixed threshold (HR > 100, RR > 24, SpO2 < 90)
    fires before ~03:50.
    """

    HR_DELTA = [(14, 0), (20, 2), (22, 5), (23 + 40 / 60, 13), (25.5, 21), (26 + 14 / 60, 27), (FIXED_ALARM_CLEAR_UNTIL_H, 34),
                (29, 37), (30.5, 38), (32.5, 38), (36, 0)]  # fmt: skip
    RR_DELTA = [(14, 0), (22, 1.5), (23 + 40 / 60, 3), (25.5, 4.75), (26 + 14 / 60, 6), (FIXED_ALARM_CLEAR_UNTIL_H, 7.5),
                (29, 9), (30.5, 9.5), (32.5, 9.5), (36, 0)]  # fmt: skip
    SKIN_DELTA = [
        (14, 0),
        (22, 0.35),
        (23 + 40 / 60, 0.65),
        (26 + 14 / 60, 0.8),
        (28, 1.0),
        (30.5, 1.1),
        (32.5, 1.1),
        (36, 0),
    ]
    SPO2_DELTA = [(14, 0), (25, -1), (27, -2), (30.5, -2.5), (32.5, -2.5), (36, 0)]
    # Restlessness in bed: turning and fidgeting this many times her usual rate, rising through the night.
    RESTLESS = [(20, 1.5), (22, 2.5), (23 + 40 / 60, 4.0), (25.5, 6.0), (26 + 14 / 60, 7.0), (31, 7.0)]
    NIGHT_MOTION = 3.0  # micro-movement while asleep vs her usual
    # Call-light toileting trips (start hour, minutes out of bed); the CNA is there first.
    TRIPS = [(22 + 50 / 60, 6), (27 + 10 / 60, 5)]
    FIDGET_BOOST = 3.0  # × her usual fidget count, on top of the RESTLESS weighting
    # The 02:00 round reaches her late: her round check is at 02:17, not ~01:40, so no 30-min post-check
    # suppression sits on her climb before 02:14.
    ROUND_0200 = 26 + 17 / 60
    NOTE_2130 = "Rosa was a bit restless at bedtime, only drank half her fluids."
    NOTE_0220 = "Rosa seems more confused than usual, warm to touch."

    def __init__(self, night: date, tz: ZoneInfo):
        super().__init__(17, night, tz, start_h=14)

    def restless(self, epoch: np.ndarray | float) -> np.ndarray | float:
        return _knots(np.asarray(epoch, dtype=float), self.pts(self.RESTLESS))

    def adjust_plan(self, plan: DayPlan, params: PhysioParams, tz: ZoneInfo) -> None:
        if not self.is_night_plan(plan):
            return
        self.replace_trips(plan, params, [Trip(self.at(h), self.at(h) + m * 60, assisted=True) for h, m in self.TRIPS])
        plan.night_motion = self.NIGHT_MOTION
        rng = np.random.default_rng([plan.day.toordinal(), 17, 404])
        s, wake = plan.sleep
        # Turning: her usual cadence divided by the restlessness factor at each moment.
        turns, t = [], s + rng.uniform(0.3, 0.7) * params.turn_cadence_min * 60 / float(self.restless(s))
        while t < wake:
            turns.append(float(t))
            t += params.turn_cadence_min * 60 / float(self.restless(t)) * rng.uniform(0.7, 1.3)
        plan.turns = turns
        # Fidgets: her usual count, scaled up, placed more densely as the restlessness rises.
        grid = np.linspace(s, wake - 60, 400)  # + up to 60 s jitter below: every fidget stays in bed
        weight = self.restless(grid)
        n = int(FIDGETS_PER_NIGHT * params.restless * self.FIDGET_BOOST * float(weight.mean()))
        plan.fidgets = sorted(
            float(x) for x in rng.choice(grid, size=n, p=weight / weight.sum()) + rng.uniform(0, 60, n)
        )
        self.drop_routine_notes(plan)
        mc_eve, mc_night = fac.staff_on_duty("CNA", 21, "Memory Care"), fac.staff_on_duty("CNA", 2, "Memory Care")
        rn_night = fac.staff_on_duty("RN", 4)
        kept = []
        round_0200 = False
        for e in plan.events:
            if e.type == "intake_logged" and e.payload.get("meal") == "dinner":
                e = e._replace(payload={**e.payload, "meal_pct": 50, "fluid_ml": 120})
            elif e.type == "intake_logged" and e.payload.get("kind") == "fluids" and e.ts < self.at(24):
                e = e._replace(payload={**e.payload, "fluid_ml": 90})
            elif e.type == "check_logged" and e.payload.get("round") == "02:00":
                e = e._replace(ts=self.at(self.ROUND_0200))
                round_0200 = True
            elif e.type == "spot_check":
                evening = e.ts < self.at(24)
                temp, sbp = (37.7, 108) if evening else (38.4, 104)
                e = e._replace(payload={**e.payload, "temp_c": temp, "sbp": sbp, "dbp": round(sbp * 0.56)})
            kept.append(e)
        if not round_0200:  # the generator skips a few rounds; hers must happen (it finds the confusion)
            kept.append(self.event(self.ROUND_0200, "check_logged", mc_night, {"kind": "round", "round": "02:00"}))
        plan.events[:] = kept + [
            self.event(21.5, "note_added", mc_eve, {"text": self.NOTE_2130, "author_role": "CNA", "source": "text"}),
            self.event(26 + 20 / 60, "note_added", mc_night,
                       {"text": self.NOTE_0220, "author_role": "CNA", "source": "text"}),
            self.event(28 + 5 / 60, "check_logged", rn_night, {"kind": "assessment"}),
            self.event(28 + 6 / 60, "spot_check", rn_night, {"sbp": 102, "dbp": 58, "temp_c": 38.2, "method": "oral"}),
            self.event(28 + 8 / 60, "note_added", rn_night, {
                "text": "Rosa: heart rate up, warm to touch (38.2 °C oral), more confused and restless. Provider "
                        "notified; urinalysis and labs ordered, encouraging fluids.",
                "author_role": "RN", "source": "text"}),
        ]  # fmt: skip

    def apply(self, sig: Signals) -> None:
        t, c = sig.t, sig.cols
        clear_until = self.at(FIXED_ALARM_CLEAR_UNTIL_H)
        before = t < clear_until

        hr = c["hr"] + _knots(t, self.pts(self.HR_DELTA))
        soft = np.where(hr > 96, 96 + (hr - 96) * 0.35, hr)
        c["hr"] = np.where(before, np.minimum(soft, 99.4), hr)

        rr = c["rr"] + _knots(t, self.pts(self.RR_DELTA))
        soft = np.where(rr > 23, 23 + (rr - 23) * 0.35, rr)
        c["rr"] = np.where(before, np.minimum(soft, 24.0), rr)

        c["skin_temp"] = c["skin_temp"] + _knots(t, self.pts(self.SKIN_DELTA))
        c["spo2"] = np.maximum(c["spo2"] + _knots(t, self.pts(self.SPO2_DELTA)), 92)
        drift = np.clip(_knots(t, self.pts(self.HR_DELTA)) / 27, 0, 1)
        c["hrv_rmssd"] = c["hrv_rmssd"] * (1 - 0.45 * drift)


class LeeFluidOverload(_NightScript):
    """Henry Lee (42): CHF fluid overload on metoprolol. RR 17 → 26, crossing 25 around 03:00; SpO2 95 → 90 by
    ~03:15; HR only +6 (beta-blocker masking). RN assessment at 03:20; O2 and head of bed raised → partial
    improvement. (Phase 9: the climb was ~45 min earlier; with P_trend live, his RR forecast then held him above
    Alvarez at 02:14. From 04:30 on the curve is unchanged.)"""

    RN_H = 27 + 20 / 60  # 03:20 RN assessment; O2 started with the 03:25 note
    # gentle until 01:45, a visible step to 02:15 (the forecast asked ~02:28 sees it), then easing to 25 by ~03:00
    RR_DELTA = [(21, 0), (23, 1), (24, 2), (25, 3), (25.5, 3.8), (25.75, 4.2), (26.25, 6.6), (26.5, 7.3), (27, 9), (27.5, 10),
                (27 + 55 / 60, 10), (28.5, 7.6), (30.5, 6), (32.5, 5), (36, 0)]  # fmt: skip
    SPO2_DELTA = [(21, 0), (23, -0.5), (24.5, -1), (25.5, -1.5), (26.25, -2), (26.75, -3.5), (27, -4.5), (27.25, -5),
                  (RN_H + 5 / 60, -5), (27 + 55 / 60, -2.3), (28.5, -2 + 1 / 3), (30.5, -1), (36, 0)]  # fmt: skip
    # (28.5, …) points sit on the pre-Phase-9 lines, so 04:30 onward is unchanged
    HR_DELTA = [(21, 0), (27, 6), (RN_H + 5 / 60, 6), (28.5, 6 - 2 * (28.5 - 27 - 5 / 60) / (30.5 - 27 - 5 / 60)),
                (30.5, 4), (36, 0)]  # fmt: skip

    def __init__(self, night: date, tz: ZoneInfo):
        super().__init__(42, night, tz, start_h=21)

    def adjust_plan(self, plan: DayPlan, params: PhysioParams, tz: ZoneInfo) -> None:
        if not self.is_night_plan(plan):
            return
        # One assisted trip early in the night, then he stays in bed (so the bed sensor sees the whole climb).
        self.replace_trips(plan, params, [Trip(self.at(22 + 50 / 60), self.at(22 + 55 / 60), assisted=True)])
        self.drop_routine_notes(plan)
        rn = fac.staff_on_duty("RN", 3)
        plan.events += [
            self.event(self.RN_H, "check_logged", rn, {"kind": "assessment"}),
            self.event(self.RN_H + 2 / 60, "spot_check", rn, {"sbp": 146, "dbp": 84, "temp_c": 36.9, "method": "oral"}),
            self.event(self.RN_H + 5 / 60, "note_added", rn, {
                "text": "Henry: breathing faster and more labored, crackles at both lung bases, ankles more swollen "
                        "than yesterday. Head of bed raised, O2 2 L/min started per standing order. Provider "
                        "notified; DNR, hospital transfer OK per care plan.",
                "author_role": "RN", "source": "text"}),
        ]  # fmt: skip

    def apply(self, sig: Signals) -> None:
        t, c = sig.t, sig.cols
        c["rr"] = np.minimum(c["rr"] + _knots(t, self.pts(self.RR_DELTA)), 27.4)
        c["spo2"] = np.maximum(c["spo2"] + _knots(t, self.pts(self.SPO2_DELTA)), 89)
        c["hr"] = c["hr"] + _knots(t, self.pts(self.HR_DELTA))


class PatelDecline(_NightScript):
    """Arthur Patel (101), hospice / POLST comfort care: from ~02:25 no turning or shifting far beyond his
    pattern; 03:00–04:30 HR 64 → 46 and RR 12 → 8. RN comfort check at 04:36, per POLST."""

    STILL_FROM_H = 26 + 25 / 60
    HR_DELTA = [(27, 0), (28.5, -17), (30.5, -17), (34, -9), (36, 0)]
    RR_DELTA = [(27, 0), (28.5, -4), (30.5, -4), (34, -2), (36, 0)]

    def __init__(self, night: date, tz: ZoneInfo):
        super().__init__(101, night, tz, start_h=self.STILL_FROM_H)

    def adjust_plan(self, plan: DayPlan, params: PhysioParams, tz: ZoneInfo) -> None:
        if not self.is_night_plan(plan):
            return
        still_from, still_to = self.at(self.STILL_FROM_H), self.at(30.5)
        comfort_turn = self.at(28 + 38 / 60)
        plan.sleep = (plan.sleep[0], max(plan.sleep[1], self.at(32)))  # does not wake this morning
        quiet = [still_from, still_to]
        plan.turns = [x for x in plan.turns if not quiet[0] <= x < quiet[1]]
        plan.fidgets = [x for x in plan.fidgets if not quiet[0] <= x < quiet[1]]
        plan.staff_turns = [x for x in plan.staff_turns if not quiet[0] <= x < quiet[1]] + [comfort_turn]
        events = []
        for e in plan.events:  # rounds in the quiet window: looked in, appeared asleep, not repositioned
            if e.type == "check_logged" and still_from <= e.ts < still_to and e.payload.get("repositioned"):
                e = e._replace(payload={k: v for k, v in e.payload.items() if k != "repositioned"})
            events.append(e)
        self.drop_routine_notes(plan)
        rn = fac.staff_on_duty("RN", 4)
        events += [
            self.event(28 + 36 / 60, "check_logged", rn, {"kind": "comfort_check", "repositioned": True}),
            self.event(28 + 40 / 60, "note_added", rn, {
                "text": "Arthur: breathing slow and shallow, not rousing to voice, appears comfortable. Comfort "
                        "measures per POLST: repositioned, mouth care, no CPR. Hospice nurse and daughter notified.",
                "author_role": "RN", "source": "text"}),
        ]  # fmt: skip
        plan.events[:] = events

    def apply(self, sig: Signals) -> None:
        t, c = sig.t, sig.cols
        quiet = (t >= self.at(self.STILL_FROM_H)) & (t < self.at(28 + 38 / 60))
        c["motion"] = np.where(quiet, c["motion"] * 0.3, c["motion"])
        c["bed_motion"] = np.where(quiet, c["bed_motion"] * 0.5, c["bed_motion"])
        c["hr"] = np.maximum(c["hr"] + _knots(t, self.pts(self.HR_DELTA)), 43)
        c["rr"] = np.maximum(c["rr"] + _knots(t, self.pts(self.RR_DELTA)), 7.0)


class OrtizFall(_NightScript):
    """James Ortiz (110), Morse 75: 04:05 unassisted bathroom trip → impact at 04:06:15 → still on the
    floor until the CNA finds him at 04:11:40 → helped back to bed by 04:16."""

    FALL_H = 28 + 6.25 / 60
    FOUND_H = 28 + (11 + 40 / 60) / 60

    def __init__(self, night: date, tz: ZoneInfo):
        super().__init__(110, night, tz, start_h=self.FALL_H)

    def adjust_plan(self, plan: DayPlan, params: PhysioParams, tz: ZoneInfo) -> None:
        if not self.is_night_plan(plan):
            return
        fall_trip = Trip(
            self.at(28 + 5 / 60), self.at(28 + 16 / 60), fall_at=self.at(self.FALL_H), found_at=self.at(self.FOUND_H)
        )
        self.replace_trips(plan, params, [Trip(self.at(25 + 10 / 60), self.at(25 + 15 / 60), assisted=True), fall_trip])
        self.drop_routine_notes(plan)
        cna, lpn, rn = fac.staff_on_duty("CNA", 4, "West"), fac.staff_on_duty("LPN", 4), fac.staff_on_duty("RN", 4)
        plan.events += [
            self.event(self.FOUND_H, "check_logged", cna, {"kind": "fall_response"}),
            self.event(28 + 20 / 60, "spot_check", lpn, {"sbp": 102, "dbp": 60, "temp_c": 36.6, "method": "oral"}),
            self.event(28.5, "note_added", rn, {
                "text": "James found on the floor beside his bed at 04:11 after trying to walk to the bathroom alone. "
                        "Helped back to bed by 2 staff. No visible injury, moving all limbs, denies hitting his head. "
                        "Neuro checks started; provider and son notified.",
                "author_role": "RN", "source": "text"}),
        ]  # fmt: skip

    def apply(self, sig: Signals) -> None:
        t, c = sig.t, sig.cols
        since = (t - self.at(self.FALL_H)).astype(float)
        stress = np.where(since >= 0, 18 * np.exp(-np.maximum(since, 0) / 1200), 0.0)
        c["hr"] = c["hr"] + stress


class WrightBandOff(_NightScript):
    """Doris Wright (33): wristband off 01:00–05:00 (a device task, not an alarm)."""

    def __init__(self, night: date, tz: ZoneInfo):
        super().__init__(33, night, tz, start_h=25, end_h=29)

    def adjust_plan(self, plan: DayPlan, params: PhysioParams, tz: ZoneInfo) -> None:
        if not self.is_night_plan(plan):
            return
        plan.band_off.append((self.at(25), self.at(29)))
        self.drop_routine_notes(plan)
        cna = fac.staff_on_duty("CNA", 5, "East")
        plan.events += [
            self.event(29.0, "check_logged", cna, {"kind": "device", "reason": "wristband reapplied"}),
            self.event(29 + 2 / 60, "note_added", cna, {
                "text": "Doris's wristband was on the bedside table; reapplied. She says it felt itchy.",
                "author_role": "CNA", "source": "text"}),
        ]  # fmt: skip


class QuietNight(Scenario):
    """Everyone whose script does not choreograph their trips: last night's bathroom trips are call-light
    assisted and there are no routine notes, so the replay's story residents are not drowned out by noise."""

    def __init__(self, resident_id: int, night: date):
        self.resident_id = resident_id
        self.night = night

    def adjust_plan(self, plan: DayPlan, params: PhysioParams, tz: ZoneInfo) -> None:
        if plan.day != self.night:
            return
        midnight = fac.local_epoch(self.night, 24, tz)
        for trip in plan.trips:
            if not trip.assisted:
                trip.assisted = True
                plan.events.append(assist_check(params.resident_id, params.unit, trip.start - 120, midnight))
        _NightScript.drop_routine_notes(plan)


CAST_SCRIPTS = (AlvarezDrift, LeeFluidOverload, PatelDecline, OrtizFall, WrightBandOff)
UNASSISTED_TONIGHT = (110,)  # only Ortiz gets up alone (his fall); everyone else's trips are call-light assisted


def scripted_night(night: date, tz: ZoneInfo) -> dict[int, list[Scenario]]:
    """Scenarios per resident for the scripted night whose evening date is `night`."""
    scenarios: dict[int, list[Scenario]] = {rid: [] for rid in fac.RESIDENT_IDS}
    for cls in CAST_SCRIPTS:
        sc = cls(night, tz)
        scenarios[sc.resident_id].append(sc)
    for rid in fac.RESIDENT_IDS:
        if rid not in UNASSISTED_TONIGHT:
            scenarios[rid].append(QuietNight(rid, night))
    return scenarios


# Replay bookmarks (SPEC §4): (hours after the night's evening midnight, resident, label). Times are the gated
# replay's (Phase 9, with P_trend): Alvarez WATCH and #1 at 23:12, HIGH at 01:34, #1 (from 01:12) and HIGH 63 at
# 02:14 when her late round is due, the 02:20 note once it reaches her; Lee's first RR forecast crossing (25 by
# ~04:15) is known at 02:30, before his RR reaches 25 (~03:00).
BOOKMARKS = (
    (23 + 12 / 60, 17, "Alvarez first deviation (WATCH)"),
    (25 + 34 / 60, 17, "Alvarez reaches HIGH"),
    (26 + 14 / 60, 17, "Alvarez at #1"),
    (26 + 20 / 60, 17, "Alvarez confusion noted"),
    (26.5, 42, "Lee forecast warning"),
    (27 + 20 / 60, 101, "Patel immobility"),
    (28 + 5 / 60, 110, "Ortiz fall"),
)


def bookmarks(night: date, tz: ZoneInfo) -> list[dict]:
    return [
        {"ts": fac.utc_iso(fac.local_epoch(night, h, tz)), "resident_id": rid, "label": label}
        for h, rid, label in BOOKMARKS
    ]


def scripted_window(night: date, tz: ZoneInfo) -> dict[str, float]:
    """Epoch bounds of the replay night (22:00–06:30) and of the baseline holdout (noon → noon)."""
    return {
        "night_start": fac.local_epoch(night, 22, tz),
        "night_end": fac.local_epoch(night, 30.5, tz),
        "holdout_start": fac.local_epoch(night, 12, tz),
        "holdout_end": fac.local_epoch(night, 36, tz),
    }


def build_sims(
    seed: int,
    tz: ZoneInfo,
    roster: list[Resident],
    night: date | None,
    live_resting_hr: float | None = None,
) -> dict[int, ResidentSim]:
    scripts = scripted_night(night, tz) if night else {}
    return {
        r.resident_id: ResidentSim(resident_params(seed, r, live_resting_hr), seed, tz, scripts.get(r.resident_id, []))
        for r in roster
    }
