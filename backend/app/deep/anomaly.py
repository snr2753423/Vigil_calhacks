"""SNOWFLAKE.ML.ANOMALY_DETECTION per closed 15-min bucket (SPEC §9 ②).

Five multi-series models (`ML.AD_HR`, `AD_RR`, `AD_SPO2`, `AD_SKIN_TEMP`, `AD_MOTION`; one series per
resident, exogenous IN_BED_FRAC) are trained by `scripts/train_models.py`. Detection runs inside Snowflake
on CORE.VITALS_15M with a 0.99 prediction interval; the five calls go out in parallel.

Severity per metric is Vigil's own deterministic mapping of the model's PERCENTILE (the observed value's
position in the forecast distribution): 0 anywhere inside the 99 % interval, rising linearly to 1 at
|z| = Z_FULL, on the clinically relevant side only (low SpO₂, high movement). Results go to
CORE.ANOMALY_RESULTS and are mirrored to Tiger `ai_insights` (kind = anomaly), where the fast tier reads
them as P_anom. A bucket's result is stamped `as_of` = bucket end + REVEAL_DELAY, the earliest moment the
live pipeline can know it, so replay reveals it no sooner than live would.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import NormalDist

import pandas as pd
import psycopg

from app.config import get_settings
from app.db import snowflake_sql as sf
from app.db import tiger
from app.deep.sync import BUCKET, BUCKET_MARGIN, VITALS_WM, naive_utc
from app.scoring.baseline import DIRECTION

log = logging.getLogger(__name__)

# metric → (model in schema ML, target column in CORE.VITALS_15M)
MODELS: dict[str, tuple[str, str]] = {
    "hr": ("AD_HR", "HR_AVG"),
    "rr": ("AD_RR", "RR_AVG"),
    "spo2": ("AD_SPO2", "SPO2_MIN"),
    "skin_temp": ("AD_SKIN_TEMP", "SKIN_TEMP_AVG"),
    "motion": ("AD_MOTION", "MOTION_SUM"),
}
EXOGENOUS = "IN_BED_FRAC"
# Where detection reads each metric from: the rows its model was trained on. Movement is night-time
# restlessness in bed only, defined once in ML.MOTION_NIGHT_IN_BED (004_models.sql).
SOURCES = {"motion": "ML.MOTION_NIGHT_IN_BED"}
DEFAULT_SOURCE = "CORE.VITALS_15M"
PREDICTION_INTERVAL = 0.99
Z_INTERVAL = NormalDist().inv_cdf(0.5 + PREDICTION_INTERVAL / 2)  # 2.576: edge of the 99 % interval
Z_FULL = 5.0  # severity reaches 1 this many σ from the model's forecast
# Live, a bucket is synced BUCKET_MARGIN (5 min) after it closes and the five DETECT_ANOMALIES calls take
# ~2–4 min on an XSMALL warehouse: results are known ~10 min after the bucket ends. Replay reveals them then.
REVEAL_DELAY = BUCKET_MARGIN + timedelta(minutes=5)
ANOMALY_WM = "anomaly"
MAX_PENDING = timedelta(hours=6)  # the background loop never re-detects more than this at once
# DETECT_ANOMALIES conditions on the input's own preceding buckets: a window that starts cold scores its first
# hours badly (everyone "anomalous" at its start). Every call therefore gets this much lead-in (never before
# the end of the training data); results for the lead-in are discarded.
CONTEXT = timedelta(hours=24)
META_KEY = "anomaly_models"
LAST_RUN_KEY = "anomaly_last_run"


# --------------------------------------------------------------------------- severity (pure)


def signed_z(percentile: float | None, distance: float | None) -> float | None:
    """Standard-normal z of the observation in the model's forecast distribution. PERCENTILE is the CDF
    value; when it saturates at 0 or 1 (float precision), DISTANCE (also in σ) takes over."""
    if percentile is not None and not math.isnan(percentile) and 0.0 < percentile < 1.0:
        return NormalDist().inv_cdf(percentile)
    if distance is not None and not math.isnan(distance):
        return float(distance)
    return None


def severity(z: float | None, metric: str) -> float:
    """0 inside the 99 % prediction interval, linear to 1 at |z| = Z_FULL, one-sided where only one side
    of normal is a concern (SPEC §9 ②: 'scaling to 1 at extreme tails')."""
    if z is None:
        return 0.0
    d = DIRECTION.get(metric, 0)
    if d > 0:
        z = max(z, 0.0)
    elif d < 0:
        z = min(z, 0.0)
    return min(max((abs(z) - Z_INTERVAL) / (Z_FULL - Z_INTERVAL), 0.0), 1.0)


def combined(severities) -> float:
    """1 − Π(1 − severity_m) (SPEC §7d P_anom)."""
    prod = 1.0
    for s in severities:
        prod *= 1.0 - s
    return 1.0 - prod


def series_id(value) -> int:
    """DETECT_ANOMALIES returns SERIES as VARIANT text ('17' or '"17"')."""
    if isinstance(value, int | float):
        return int(value)
    return int(json.loads(value)) if isinstance(value, str) and value.startswith('"') else int(value)


def _f(v) -> float | None:
    return None if v is None else float(v)


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 3)


def parse_rows(metric: str, cols: list[str], rows: list[tuple]) -> list[dict]:
    """DETECT_ANOMALIES output rows → dicts with Vigil's z and severity."""
    idx = {c.upper(): i for i, c in enumerate(cols)}
    out = []
    for r in rows:
        z = signed_z(_f(r[idx["PERCENTILE"]]), _f(r[idx["DISTANCE"]]))
        ts = r[idx["TS"]]
        out.append({
            "resident_id": series_id(r[idx["SERIES"]]),
            "bucket": ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC),
            "metric": metric,
            "y": _f(r[idx["Y"]]),
            "forecast": _f(r[idx["FORECAST"]]),
            "lower": _f(r[idx["LOWER_BOUND"]]),
            "upper": _f(r[idx["UPPER_BOUND"]]),
            "is_anomaly": bool(r[idx["IS_ANOMALY"]]),
            "percentile": _f(r[idx["PERCENTILE"]]),
            "distance": _f(r[idx["DISTANCE"]]),
            "z": None if z is None else round(z, 3),
            "severity": round(severity(z, metric), 4),
        })  # fmt: skip
    return out


def build_payloads(results: list[dict]) -> dict[tuple[int, datetime], dict]:
    """Per (resident, bucket): every metric's result, the anomalous metrics and the combined severity.
    This is what Tiger ai_insights stores and what P_anom reads."""
    grouped: dict[tuple[int, datetime], dict] = {}
    for r in results:
        key = (r["resident_id"], r["bucket"])
        p = grouped.setdefault(key, {"bucket": r["bucket"].isoformat(), "metrics": {}})
        p["metrics"][r["metric"]] = {k: _round(r[k]) for k in ("y", "forecast", "lower", "upper", "z", "severity")} | {
            "is_anomaly": r["is_anomaly"]
        }
    for p in grouped.values():
        m = p["metrics"]
        p["anomalous"] = sorted(k for k, v in m.items() if v["severity"] > 0)
        p["p_anom"] = round(combined(v["severity"] for v in m.values()), 4)
    return grouped


def results_frame(results: list[dict], detected_at: datetime) -> pd.DataFrame:
    df = pd.DataFrame(
        [
            (r["resident_id"], r["metric"], naive_utc(r["bucket"]), r["y"], r["forecast"], r["lower"], r["upper"],
             r["is_anomaly"], r["percentile"], r["distance"], r["severity"], "ML." + MODELS[r["metric"]][0])
            for r in results
        ],
        columns=["RESIDENT_ID", "METRIC", "BUCKET_TS", "Y", "FORECAST", "LOWER_BOUND", "UPPER_BOUND", "IS_ANOMALY",
                 "PERCENTILE", "DISTANCE", "SEVERITY", "MODEL"],
    )  # fmt: skip
    df["BUCKET_TS"] = df["BUCKET_TS"].astype("datetime64[us]")
    df["DETECTED_AT"] = pd.Series([naive_utc(detected_at)] * len(df), dtype="datetime64[us]")
    return df


# --------------------------------------------------------------------------- Snowflake calls


def _lit(ts: datetime) -> str:
    """A TIMESTAMP_NTZ literal (UTC), quoted for use inside SYSTEM$QUERY_REFERENCE('…')."""
    return f"''{naive_utc(ts):%Y-%m-%d %H:%M:%S}''::TIMESTAMP_NTZ"


def detect_sql(metric: str, start: datetime, end: datetime) -> str:
    """CALL …!DETECT_ANOMALIES over buckets in [start, end) for every resident (start includes any lead-in)."""
    model, target = MODELS[metric]
    query = (
        f"SELECT RESIDENT_ID, BUCKET_TS, {target}, {EXOGENOUS} FROM {SOURCES.get(metric, DEFAULT_SOURCE)} "
        f"WHERE BUCKET_TS >= {_lit(start)} AND BUCKET_TS < {_lit(end)} "
        f"AND {target} IS NOT NULL AND {EXOGENOUS} IS NOT NULL"
    )
    return (
        f"CALL ML.{model}!DETECT_ANOMALIES("
        f"INPUT_DATA => SYSTEM$QUERY_REFERENCE('{query}'), "
        f"SERIES_COLNAME => 'RESIDENT_ID', TIMESTAMP_COLNAME => 'BUCKET_TS', TARGET_COLNAME => '{target}', "
        f"CONFIG_OBJECT => {{'prediction_interval': {PREDICTION_INTERVAL}}})"
    )


@dataclass
class Detection:
    start: datetime
    end: datetime
    results: list[dict] = field(default_factory=list)
    seconds: dict[str, float] = field(default_factory=dict)  # per metric
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def run_detection(
    sconn,
    start: datetime,
    end: datetime,
    input_start: datetime | None = None,
    metrics=tuple(MODELS),
    concurrency: int | None = None,
) -> Detection:
    """Blocking: the five DETECT_ANOMALIES calls in parallel (the connector allows concurrent cursors) over
    [input_start, end); only results for [start, end) are kept. `concurrency` caps how many run at once (the live
    loop on the shared warehouse leaves room for the interactive calls)."""
    det = Detection(start, end)
    input_start = min(input_start or start, start)

    def one(metric: str) -> tuple[str, list[dict] | None, float, str | None]:
        t0 = time.perf_counter()
        try:
            sql = detect_sql(metric, input_start, end)
            cols, rows = sf.query_columns(sconn, sql, feature=f"anomaly.detect.{metric}")
            kept = [r for r in parse_rows(metric, cols, rows) if r["bucket"] >= start]
            return metric, kept, time.perf_counter() - t0, None
        except Exception as exc:  # one model failing must not lose the others
            return metric, None, time.perf_counter() - t0, " ".join(str(exc).split())[:200]

    with ThreadPoolExecutor(max_workers=max(1, min(len(metrics), concurrency or len(metrics)))) as pool:
        for metric, rows, secs, err in pool.map(one, metrics):
            det.seconds[metric] = round(secs, 2)
            if err is not None:
                det.errors[metric] = err
            else:
                det.results.extend(rows)
    return det


def _store_snowflake(sconn, det: Detection) -> int:
    """Replace ANOMALY_RESULTS rows for the range with this run's (re-detection is idempotent)."""
    ok_metrics = sorted(set(MODELS) - set(det.errors))
    if ok_metrics:
        marks = ", ".join(["%s"] * len(ok_metrics))
        sf.query(
            sconn,
            f"DELETE FROM CORE.ANOMALY_RESULTS WHERE BUCKET_TS >= %s AND BUCKET_TS < %s AND METRIC IN ({marks})",
            (naive_utc(det.start), naive_utc(det.end), *ok_metrics),
            feature="anomaly.store",
        )
    return sf.write_df(sconn, results_frame(det.results, datetime.now(UTC)), "ANOMALY_RESULTS", feature="anomaly.store")


# --------------------------------------------------------------------------- orchestration


@dataclass
class DetectStats:
    phase: str = "idle"  # idle | no models | detecting | error
    runs: int = 0
    last_run: str | None = None
    last_range: list[str] | None = None
    last_seconds: float = 0.0
    last_seconds_by_metric: dict[str, float] = field(default_factory=dict)
    last_rows: int = 0
    last_flagged: int = 0  # resident-buckets with ≥ 1 anomalous metric in the last run
    last_error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


stats = DetectStats()


async def detect_range(
    tconn: psycopg.AsyncConnection,
    sconn,
    start: datetime,
    end: datetime,
    train_to: datetime,
    *,
    concurrency: int | None = None,
    warehouse: str | None = None,
) -> Detection:
    """Detect buckets in [start, end) for all residents (with CONTEXT lead-in, never before `train_to`), store
    in Snowflake and mirror to Tiger ai_insights. `warehouse`: run the detection calls there (the session switches
    back after)."""
    t0 = time.perf_counter()
    stats.phase = "detecting"

    def detect() -> Detection:
        if not warehouse:
            return run_detection(sconn, start, end, max(train_to, start - CONTEXT), concurrency=concurrency)
        sf.query(sconn, f"USE WAREHOUSE {warehouse}", feature="anomaly.detect.warehouse")
        try:
            return run_detection(sconn, start, end, max(train_to, start - CONTEXT), concurrency=concurrency)
        finally:
            sf.query(sconn, f"USE WAREHOUSE {get_settings().snowflake_warehouse}", feature="anomaly.detect.warehouse")

    det = await asyncio.to_thread(detect)
    await asyncio.to_thread(_store_snowflake, sconn, det)
    payloads = build_payloads(det.results)
    await tiger.delete_anomaly_insights(tconn, start, end)  # re-detection replaces, never duplicates, a bucket
    await tiger.upsert_insights(
        tconn, "anomaly", ((rid, bucket + BUCKET + REVEAL_DELAY, p) for (rid, bucket), p in payloads.items())
    )
    stats.runs += 1
    stats.last_run = datetime.now(UTC).isoformat()
    stats.last_range = [start.isoformat(), end.isoformat()]
    stats.last_seconds = round(time.perf_counter() - t0, 2)
    stats.last_seconds_by_metric = det.seconds
    stats.last_rows = len(det.results)
    stats.last_flagged = sum(1 for p in payloads.values() if p["anomalous"])
    stats.last_error = "; ".join(f"{m}: {e}" for m, e in det.errors.items()) or None
    stats.phase = "error" if det.errors else "idle"
    if det.errors:
        log.warning("anomaly: %s", stats.last_error)
    # Kept in Tiger so /under-the-hood shows the newest run from any process (backend, `make detect`, prepare).
    last = {k: v for k, v in stats.to_dict().items() if k != "runs"} | {
        "residents": len({r["resident_id"] for r in det.results})
    }
    await tiger.set_meta(tconn, LAST_RUN_KEY, last)
    return det


async def models_meta(tconn: psycopg.AsyncConnection) -> dict | None:
    """Metadata `make train` stored for the current seed, or None (not trained since the last seed)."""
    meta = await tiger.get_meta(tconn, META_KEY)
    seed = await tiger.get_meta(tconn, "seed")
    if not meta or not seed or meta.get("seeded_at") != seed.get("seeded_at"):
        return None
    return meta


async def detect_pending(tconn: psycopg.AsyncConnection, sconn) -> Detection | None:
    """Detect every synced bucket not yet detected (called by the sync loop after each sync)."""
    meta = await models_meta(tconn)
    if meta is None:
        stats.phase = "no models"  # run `make train` after seeding
        return None
    state = await tiger.get_sync_state(tconn)
    vit_wm = (state.get(VITALS_WM) or {}).get("watermark")
    if vit_wm is None:
        return None
    end = vit_wm + BUCKET  # through the newest synced bucket
    det_wm = (state.get(ANOMALY_WM) or {}).get("watermark")
    start = det_wm + BUCKET if det_wm is not None else end - BUCKET
    train_to = datetime.fromisoformat(meta["train_to"])
    start = max(start, end - MAX_PENDING, train_to)
    if start >= end:
        return None
    settings = get_settings()
    own = settings.snowflake_detect_warehouse
    det = await detect_range(tconn, sconn, start, end, train_to, warehouse=own,
                             concurrency=None if own else settings.anomaly_detect_concurrency)  # fmt: skip
    if det.ok:
        await tiger.set_sync_state(tconn, ANOMALY_WM, end - BUCKET, len(det.results))
    return det
