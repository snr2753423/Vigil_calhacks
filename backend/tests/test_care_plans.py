"""Synthetic care plans (SPEC §9 ④): every resident gets every section; the cast's quotable lines exist."""

import sys

import pytest
from pypdf import PdfReader

from app.config import BACKEND_DIR
from app.sim.care_plans import SECTIONS, build_care_plan, filename
from app.sim.cast import build_roster

sys.path.insert(0, str(BACKEND_DIR / "scripts"))
import generate_care_plans  # noqa: E402

SEED = 1337


@pytest.fixture(scope="module")
def plans() -> dict:
    return {r.resident_id: build_care_plan(r, SEED) for r in build_roster(SEED)}


def _section(plan, heading: str) -> str:
    return " ".join(dict(plan.sections)[heading])


def test_every_resident_has_every_section(plans) -> None:
    assert len(plans) == 120
    for plan in plans.values():
        assert [h for h, _ in plan.sections] == list(SECTIONS)
        assert all(lines for _, lines in plan.sections)


def test_code_status_matches_the_roster(plans) -> None:
    for r in build_roster(SEED):
        code = _section(plans[r.resident_id], "Code status and advance directives")
        expect = {"FULL": "FULL code", "DNR": "DNR", "DNR-DNI": "DNR-DNI", "COMFORT": "POLST"}[r.code_status]
        assert expect in code


def test_cast_lines_are_quotable(plans) -> None:
    grant, patel, alvarez = plans[120], plans[101], plans[17]
    assert "bed alarm; assist ×1 to bathroom" in _section(grant, "Mobility and fall precautions")
    code = _section(patel, "Code status and advance directives")
    assert "POLST" in code and "comfort-focused" in code and "No CPR" in code
    assert "notify hospice + daughter Anjali" in _section(patel, "Escalation preferences")
    assert "History of UTIs presenting as confusion" in _section(alvarez, "Diagnoses and history")
    assert "FULL code" in _section(alvarez, "Code status and advance directives")
    assert "Notify provider for acute mental status change" in _section(alvarez, "Escalation preferences")


def test_pdf_renders_on_one_or_two_pages(tmp_path, plans) -> None:
    path = tmp_path / filename(17)
    generate_care_plans.render(plans[17], path)
    reader = PdfReader(str(path))
    text = " ".join(" ".join(p.extract_text().split()) for p in reader.pages)
    assert 1 <= len(reader.pages) <= 2
    assert "History of UTIs presenting as confusion" in text and "Escalation preferences" in text
    assert filename(17) == "resident_017.pdf"
