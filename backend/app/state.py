"""In-process simulation/display state shared by background tasks and the API.

Stored data is always real UTC; `display_clock_offset_s` and `force_night` only affect
the header clock and night-window checks (SPEC §4).
"""

from dataclasses import asdict, dataclass
from typing import Literal


@dataclass
class SimState:
    mode: Literal["live", "replay"] = "live"
    paused: bool = False
    force_night: bool = False
    display_clock_offset_s: int = 0
    simulated_band: bool = False  # fallback: simulate R-120 in live mode instead of the Polar band + iPhone

    def to_dict(self) -> dict:
        return asdict(self)


sim_state = SimState()
