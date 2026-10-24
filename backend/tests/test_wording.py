"""Decision-support wording guard for Cortex text (AGENTS.md rule 9)."""

import pytest

from app.deep import assess
from app.deep.wording import soften, violations


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Infection likely; check Rosa now", "Infection possible; check Rosa now"),
        ("Likely UTI with early sepsis", "Possible UTI with early sepsis"),
        ("Her confusion is likely due to infection", "Her confusion may be due to infection"),
        ("RR rising, likely to worsen", "RR rising, may worsen"),
        ("She has early sepsis", "She may have early sepsis"),
        ("Findings indicate infection", "Findings could fit possible infection"),
        ("Pattern suggests a diagnosis of UTI", "Pattern could fit possible UTI"),
        ("Probably dehydrated", "Possibly dehydrated"),
        ("She is septic", "She may be septic"),
        ("Early sepsis signs: HR 88, RR 22", "Possible early sepsis signs: HR 88, RR 22"),
        # already decision-support language: unchanged
        ("Rising HR/RR, possible early infection", "Rising HR/RR, possible early infection"),
        ("History of UTIs presenting as confusion", "History of UTIs presenting as confusion"),
        ("Consider urinary tract infection; check urine", "Consider urinary tract infection; check urine"),
        ("Sepsis risk rising, check now", "Sepsis risk rising, check now"),
        ("Mild HR/RR rise, low fluids; recent UTI history", "Mild HR/RR rise, low fluids; recent UTI history"),
        ("Heart failure; HR may not rise on metoprolol", "Heart failure; HR may not rise on metoprolol"),
    ],
)
def test_soften(text: str, expected: str) -> None:
    assert soften(text) == expected
    assert soften(expected) == expected  # idempotent
    assert violations(soften(text)) == []


def test_violations() -> None:
    assert violations("Diagnosis: UTI") == ["diagnosis"]
    assert violations("This is not a diagnosis") == []
    assert violations("Vigil prevents deaths") == ["prevents death"]


CHUNKS = [{"section": "History", "chunk": "History of UTIs presenting as confusion rather than fever."}]


def _raw(**kw) -> dict:
    base = {"headline": "Infection likely; check Rosa now", "assessment": "HR 88 vs her expected 60-70. Likely UTI.",
            "possible_concerns": ["UTI"], "recommended_checks": ["Check temperature"], "escalation": "Notify the RN.",
            "care_plan_citations": [{"section": "History", "quote": "History of UTIs presenting as confusion"}],
            "confidence": "medium", "caveats": "No fever documented."}  # fmt: skip
    return base | kw


def test_assessment_cards_are_rewritten_into_decision_support_language() -> None:
    card = assess.validate(_raw(), CHUNKS)
    assert card["headline"] == "Infection possible; check Rosa now"
    assert card["assessment"].endswith("Possible UTI.") and card["possible_concerns"] == ["Possible UTI"]
    assert card["care_plan_citations"][0]["quote"] == "History of UTIs presenting as confusion"  # verbatim, untouched
    assert assess.wording_rewrites(_raw(), card) == 2


def test_wording_that_cannot_be_rewritten_rejects_the_card() -> None:
    with pytest.raises(assess.InvalidAssessment, match="diagnosis"):
        assess.validate(_raw(headline="Diagnosis: sepsis"), CHUNKS)
