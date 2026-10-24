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


# --------------------------------------------------------------------------- Cortex assessment (Phase 6)

import asyncio  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402

from app.deep import assess  # noqa: E402

CHUNKS = [
    {"section": "Diagnoses and history", "chunk": "## Diagnoses and history\n\n- History of UTIs presenting as "
     "confusion: past urinary tract infections showed up as new or worse\nconfusion and restlessness, often "
     "without fever."},
    {"section": "Escalation preferences", "chunk": "## Escalation preferences\n\n- Notify provider for acute "
     "mental status change (new or worse confusion, agitation or unusual sleepiness)."},
]  # fmt: skip


def _raw(**over) -> dict:
    base = {
        "headline": "Rising HR and RR; check Rosa now",
        "assessment": "HR 84 vs her expected 67-79.",
        "possible_concerns": ["Possible early infection"],
        "recommended_checks": ["Check mental status vs baseline", "Check temperature"],
        "escalation": "Notify the provider for acute mental status change.",
        "care_plan_citations": [],
        "confidence": "medium",
        "caveats": "No confusion documented yet.",
    }
    return base | over


def test_validate_keeps_verbatim_citations_across_line_breaks_and_bullets() -> None:
    cites = [{"section": "Diagnoses and history",
              "quote": "History of UTIs presenting as confusion: past urinary tract infections showed up as new or "
                       "worse confusion and restlessness, often without fever."}]  # fmt: skip
    card = assess.validate(_raw(care_plan_citations=cites), CHUNKS)
    assert card["care_plan_citations"] == cites


def test_validate_drops_invented_citations_and_fixes_enums() -> None:
    cites = [{"section": "Escalation preferences", "quote": "Call 911 for any fever."},
             {"section": "", "quote": "notify provider for acute mental status change"}]  # fmt: skip
    card = assess.validate(_raw(care_plan_citations=cites, confidence="very high"), CHUNKS)
    assert [c["quote"] for c in card["care_plan_citations"]] == ["notify provider for acute mental status change"]
    assert card["care_plan_citations"][0]["section"] == "Escalation preferences"  # filled from the chunk
    assert card["confidence"] == "medium"
    assert set(card) == set(assess.ASSESSMENT_SCHEMA["required"])  # same shape as the template


def test_validate_rejects_incomplete_output() -> None:
    with pytest.raises(assess.InvalidAssessment):
        assess.validate({"headline": "x"}, CHUNKS)
    with pytest.raises(assess.InvalidAssessment):
        assess.validate(_raw(recommended_checks=[]), CHUNKS)


def test_template_has_the_cortex_shape() -> None:
    a = template_assessment(RESIDENT, _current("HIGH", [("rr", "RR 26/min")]))
    assert set(assess.ASSESSMENT_SCHEMA["required"]) <= set(a)


def test_care_plan_query_is_worded() -> None:
    cur = {"factors": [{"key": "rr", "text": "RR 21/min"}, {"key": "hr", "text": "HR 84"}], "rule": None}
    q = assess.care_plan_query({}, cur)
    assert q.startswith("breathing rate change, heart rate change:") and "diagnoses and history" in q
    assert "fall precautions" not in q
    fall = {"factors": [{"key": "rule:fall_prevention", "text": "x"}], "rule": {"rule": "fall_prevention"}}
    assert "fall precautions" in assess.care_plan_query({}, fall)


def test_describe_event_skips_vigil_alerts() -> None:
    tz = ZoneInfo("America/Los_Angeles")
    ts = datetime(2026, 10, 17, 4, 30, tzinfo=UTC)  # 21:30 PDT
    note = {"ts": ts, "type": "note_added", "payload": {"text": "a bit restless", "author_role": "CNA"}}
    assert assess.describe_event(note, tz) == '21:30 note (CNA): "a bit restless"'
    intake = {"ts": ts, "type": "intake_logged", "payload": {"meal": "dinner", "meal_pct": 50, "fluid_ml": 120}}
    assert assess.describe_event(intake, tz) == "21:30 intake: dinner 50% eaten, 120 mL fluids"
    assert assess.describe_event({"ts": ts, "type": "alert_raised", "payload": {}}, tz) is None


def test_news_values_keep_decimals_for_temperature() -> None:
    assert assess._news_value(37.7) == "37.7" and assess._news_value(86.57) == "87"
    assert assess._news_value(True) == "new confusion"


def test_scan_triggers_on_tier_rise_and_stale_top5_with_debounce() -> None:
    a = assess.Assessor()
    now = datetime(2026, 10, 17, 9, 14, tzinfo=UTC)
    assert a.scan([(17, "WATCH", 1), (42, "STABLE", 2)], now) == [(17, "top5_stale")]  # no assessment yet
    assert a.scan([(17, "HIGH", 1)], now) == []  # rose, but attempted < 2 min ago
    a.last_attempt[17] -= assess.RISE_DEBOUNCE_S
    assert a.scan([(17, "WATCH", 1)], now) == []
    assert a.scan([(17, "CRITICAL", 1)], now) == [(17, "tier_rise")]
    a.latest[42] = {"as_of": (now - timedelta(minutes=5)).isoformat(), "headline": "h"}
    assert a.scan([(42, "WATCH", 3)], now) == []  # fresh enough
    assert a.scan([(43, "WATCH", 6)], now) == []  # outside the top 5


def test_headline_respects_as_of_and_age() -> None:
    a = assess.Assessor()
    made = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
    a.latest[17] = {"as_of": made.isoformat(), "headline": "Check Rosa"}
    assert a.headline_for(17, made + timedelta(minutes=5)) == {"text": "Check Rosa", "at": made.isoformat()}
    assert a.headline_for(17, made - timedelta(minutes=1)) is None  # not known yet
    assert a.headline_for(17, made + assess.MAX_AGE + timedelta(seconds=1)) is None  # too old


def test_explain_falls_back_on_error_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(self, rid, as_of, trigger):
        raise ConnectionError("network down")

    async def slow(self, rid, as_of, trigger):
        await asyncio.sleep(5)
        return {"headline": "late"}

    async def go():
        a = assess.Assessor()
        monkeypatch.setattr(assess.Assessor, "_assess", boom)
        failed = await a.explain(17, None)
        monkeypatch.setattr(assess.Assessor, "_assess", slow)
        timed_out = await a.explain(17, None, wait_s=0.05)
        still_running = a.pending(17)
        a.inflight[(17, None)].cancel()
        return failed, timed_out, still_running, a.stats

    failed, timed_out, still_running, stats = asyncio.run(go())
    assert failed[0] is None and "unavailable" in failed[1]
    assert timed_out[0] is None and "longer than" in timed_out[1] and still_running
    assert stats.fallbacks == 2 and stats.timeouts == 1 and stats.errors == 1


def test_a_rise_during_a_running_assessment_is_not_lost() -> None:
    a = assess.Assessor()
    now = datetime(2026, 10, 17, 9, 14, tzinfo=UTC)
    assert a.scan([(17, "WATCH", 1)], now) == [(17, "top5_stale")]
    a.pending = lambda rid, as_of=None: True  # the top5_stale call is still running
    assert a.scan([(17, "CRITICAL", 1)], now) == []
    a.pending = lambda rid, as_of=None: False
    a.last_attempt[17] -= assess.RISE_DEBOUNCE_S
    assert a.scan([(17, "CRITICAL", 1)], now) == [(17, "tier_rise")]
