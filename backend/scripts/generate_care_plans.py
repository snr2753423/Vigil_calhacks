"""One synthetic 1–2 page care-plan PDF per resident (SPEC §9 ④) → data/care_plans/resident_NNN.pdf.

Content comes from `app/sim/care_plans.py` (the seeded roster), so it matches what Tiger holds.
`make care-plans` runs this, then uploads and indexes the PDFs in Snowflake (scripts/index_care_plans.py).
"""

import argparse
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

from app.config import ROOT_DIR, get_settings
from app.sim import facility as fac
from app.sim.care_plans import CarePlan, build_care_plan
from app.sim.cast import build_roster

OUT_DIR = ROOT_DIR / "data" / "care_plans"

_styles = getSampleStyleSheet()
TITLE = ParagraphStyle("t", parent=_styles["Title"], fontSize=18, spaceAfter=4, alignment=0)
FACILITY = ParagraphStyle("f", parent=_styles["Normal"], fontSize=9, textColor=colors.grey)
IDENT = ParagraphStyle("i", parent=_styles["Normal"], fontSize=10, leading=13)
H2 = ParagraphStyle("h", parent=_styles["Heading2"], fontSize=13, spaceBefore=10, spaceAfter=3)
BODY = ParagraphStyle("b", parent=_styles["Normal"], fontSize=10.5, leading=14)


def render(plan: CarePlan, path: Path) -> None:
    doc = SimpleDocTemplate(
        str(path), pagesize=LETTER, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch, title=plan.title, author=fac.FACILITY_NAME,
    )  # fmt: skip
    story = [
        Paragraph(escape(fac.FACILITY_NAME), FACILITY),
        Paragraph(escape(plan.title), TITLE),
        *(Paragraph(escape(line), IDENT) for line in plan.identity),
        Spacer(1, 4),
    ]
    for heading, lines in plan.sections:
        story.append(Paragraph(escape(heading), H2))
        items = [ListItem(Paragraph(escape(line), BODY), leftIndent=12) for line in lines]
        story.append(ListFlowable(items, bulletType="bullet", start="•", leftIndent=12))
    doc.build(story)


def generate(out_dir: Path = OUT_DIR, ids: list[int] | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for r in build_roster(get_settings().sim_seed):
        if ids and r.resident_id not in ids:
            continue
        plan = build_care_plan(r, get_settings().sim_seed)
        path = out_dir / plan.filename
        render(plan, path)
        paths.append(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--ids", type=int, nargs="*", help="only these resident ids")
    args = parser.parse_args()
    paths = generate(args.out, args.ids)
    print(f"care plans: wrote {len(paths)} PDFs to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
