"""Deterministic assessment template (the AI card's fallback, AGENTS.md rule 5)."""

from app.deep.assess import template_assessment

RESIDENT = {"code_status": "COMFORT", "profile": {"escalation": "Call hospice first."}}


def _current(tier: str, factors: list[tuple[str, str]], rule: str | None = None) -> dict:
    return {
        "tier": tier,
        "score": 100.0 if rule else 62.0,
        "rule": {"text": rule} if rule else None,
        "factors": [{"key": k, "text": t} for k, t in factors],
    }


def test_rule_leads_the_headline_and_checks_follow_factors() -> None:
    cur = _current("CRITICAL", [("rule:fall", "Suspected fall at 04:06"), ("overdue", "Not checked for 2 h")],
                   rule="Suspected fall at 04:06")  # fmt: skip
    a = template_assessment(RESIDENT, cur)
    assert a["headline"] == "Go now: Suspected fall at 04:06"
    assert a["recommended_checks"][0].startswith("Go to the resident now")
    assert "Complete a bedside check" in a["recommended_checks"]
    assert a["source"] == "template" and a["confidence"] == "low" and a["care_plan_citations"] == []


def test_escalation_respects_code_status_and_care_plan() -> None:
    a = template_assessment(RESIDENT, _current("HIGH", [("rr", "RR 26/min")]))
    assert "No CPR" in a["escalation"] and "Call hospice first." in a["escalation"]
    stable = template_assessment(RESIDENT, _current("STABLE", []))
    assert "Care plan" not in stable["escalation"] and stable["recommended_checks"] == [
        "Routine check on the next round"
    ]


def test_no_score_still_renders() -> None:
    a = template_assessment({"code_status": "FULL"}, None)
    assert a["headline"] and a["recommended_checks"]


def test_never_uses_diagnostic_language() -> None:
    a = template_assessment(RESIDENT, _current("HIGH", [("concern", "New confusion (note 21:30)")]))
    text = " ".join(str(v) for v in a.values()).lower()
    assert "diagnos" not in text.replace("not a clinical assessment", "")
