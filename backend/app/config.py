"""Settings loaded from the repo-root `.env` (or the process environment, e.g. in Docker)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Tiger Cloud (TimescaleDB)
    tiger_database_url: str | None = None

    # Snowflake (key-pair auth)
    snowflake_account: str | None = None
    snowflake_user: str | None = None
    snowflake_private_key_path: str = "secrets/snowflake_rsa_key.p8"
    snowflake_role: str = "VIGIL_ROLE"
    snowflake_warehouse: str = "VIGIL_WH"
    snowflake_database: str = "VIGIL"
    cortex_model: str = "claude-sonnet-5"

    # Simulation / facility
    seed_days: int = 3
    facility_tz: str = "America/Los_Angeles"
    sim_seed: int = 1337  # seeded RNG: same seed → same residents, same history
    sim_live_enabled: bool = True  # stream simulated residents into Tiger while the backend runs
    sim_backfill_max_hours: float = 48.0  # on startup, fill at most this much of a gap since the last row
    r120_resting_hr: float | None = None  # presenter's measured resting HR, used for R-120's seeded history

    # Fast-tier evaluator (SPEC §7a)
    evaluator_enabled: bool = True
    eval_interval_s: float = 2.0
    snapshot_interval_s: float = 10.0  # full priority_snapshots write cadence (tier changes are written at once)

    # Deep tier: Tiger → Snowflake sync + ANOMALY_DETECTION (SPEC §9 ① ②)
    snowflake_sync_enabled: bool = True  # background sync (every SYNC_INTERVAL_S) + detection per closed bucket
    sync_interval_s: float = 60.0
    anomaly_detect_enabled: bool = True
    # Training window: up to this many days, ending where last night's holdout starts (SPEC: days −14…−2).
    # Clamped to the seeded history, so a 3-day seed trains on ~2 days.
    anomaly_train_days: float = 12.0
    snowflake_train_warehouse: str | None = None  # optional bigger warehouse for `make train` only

    # Deep tier: Cortex AI assessment (SPEC §9 ⑤): automatic triggers; Refresh works whenever Snowflake is set
    assess_enabled: bool = True

    # Live WebSocket
    heartbeat_interval_s: float = 2.0

    # Live hardware (SPEC §10): the public HTTPS origin (cloudflared tunnel or the Vultr domain) for the
    # iPhone bed-sensor QR code on /devices. Re-read on each request, so no restart is needed after editing.
    public_url: str | None = None
    device_ingest_enabled: bool = True  # the 1-s device writer (WS /ws/ingest still answers when off)

    @property
    def snowflake_private_key_file(self) -> Path:
        """Key path; relative paths resolve against the repo root."""
        path = Path(self.snowflake_private_key_path).expanduser()
        return path if path.is_absolute() else ROOT_DIR / path

    @property
    def tiger_configured(self) -> bool:
        return bool(self.tiger_database_url)

    @property
    def snowflake_configured(self) -> bool:
        return bool(self.snowflake_account and self.snowflake_user)


@lru_cache
def get_settings() -> Settings:
    return Settings()
