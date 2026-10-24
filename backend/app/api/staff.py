"""GET /api/staff — the staff roster with who is on duty now (header "staff on duty", SPEC §11.1)."""

from datetime import UTC, datetime

from fastapi import APIRouter

from app.config import get_settings
from app.scoring import evaluator as ev
from app.sim import facility as fac

router = APIRouter(prefix="/api", tags=["staff"])


@router.get("/staff")
async def list_staff() -> dict:
    """On duty by the displayed facility clock (honours the demo's display offset)."""
    clock = ev.night_clock(fac.tzinfo(get_settings().facility_tz))
    local = clock.display(datetime.now(UTC))
    shift = fac.shift_at(local.hour + local.minute / 60)
    return {
        "shift": shift,
        "staff": [
            {"staff_id": s.staff_id, "name": s.name, "role": s.role, "shift": s.shift, "unit": s.unit,
             "on_duty": s.shift == shift}
            for s in fac.STAFF
        ],
    }  # fmt: skip
