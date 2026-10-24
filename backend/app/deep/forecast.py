"""SNOWFLAKE.ML.FORECAST on a resident entering HIGH/CRITICAL (SPEC §9 ③).

For one resident at one time: three single-series models (RR_AVG, SPO2_MIN, HR_AVG from CORE.VITALS_15M), trained
in parallel inside Snowflake, then `!FORECAST` 16 periods (4 h) with a 90 % interval. The models are dropped
after use. The result goes to CORE.FORECAST_RESULTS and to Tiger `ai_insights` (kind = forecast, as_of = when
it became known), where the fast tier reads it as P_trend (`scoring/trend.py`) and the charts draw it.

Training window: the trailing FORECAST_TRAIN_HOURS (4 h) of closed buckets, not SPEC's 48 h. Measured on Lee's
night: RR is recorded only while in bed, so 48 h is three separate nights with daytime gaps, and the forecast stayed
flat at 20.3/min while his RR climbed. At 02:28, with RR rising ~0.9/min per bucket, 6, 8 and 12 h windows all
returned flat persistence (21.8 for 4 h); 4 h followed the climb (25 by ~04:15). The cost: late in a steep rise a
4-h window over-extrapolates (at 03:00 it projected RR 31 within 2 h). `FORECAST_TRAIN_HOURS` changes it.

The chart shows the first 2 h (SPEC: 8 periods); the extra 2 h exist only so P_trend can be 0.5 ("crosses within
4 h", SPEC §7d).

Forecasts run on SNOWFLAKE_TRAIN_WAREHOUSE when it is set (`snowflake_sql.ml_warehouse`): a training is ~50 s of a
warehouse, and on the shared XSMALL one three of them queued the interactive AI_EXTRACT / Cortex calls past their
timeouts.

Triggers (live, `Forecaster`): the evaluator's alert into HIGH/CRITICAL (`on_alert`), and every SCAN_S a scan
that refreshes residents still HIGH/CRITICAL whose forecast is older than REFRESH (30 min). Replay never calls
Snowflake: `prepare_replay_forecasts` makes the same forecasts ahead of time at each entry into HIGH/CRITICAL on
last night (and every 30 min while it lasts), stamped with when live would have known them.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg

from app.config import get_settings
from app.db import snowflake_sql as sf
from app.db import tiger
from app.deep.sync import BUCKET_MARGIN, naive_utc
from app.scoring import trend
from app.ws.hub import hub

log = logging.getLogger(__name__)

# metric → CORE.VITALS_15M column (the same columns ANOMALY_DETECTION models)
METRICS: dict[str, str] = {"rr": "RR_AVG", "spo2": "SPO2_MIN", "hr": "HR_AVG"}
PERIODS = 16  # 4 h of 15-min buckets; the chart draws CHART_PERIODS
CHART_PERIODS = 8
PREDICTION_INTERVAL = 0.9
MIN_ROWS = 12  # fewer closed buckets than this (3 h) in the window: no forecast for that metric
# Measured on this account: training ~50 s and forecasting ~10 s per metric (the three run in parallel), so a
# forecast is known ~1–1.5 min after it is asked for. Replay reveals prepared forecasts this long after the ask.
REVEAL_DELAY = timedelta(minutes=2)
REFRESH = timedelta(minutes=30)  # re-forecast a resident still HIGH/CRITICAL this often
SCAN_S = 30.0
CONCURRENCY = 2  # residents forecast at once live (each runs 3 models in parallel)
PREPARE_CONCURRENCY = 4
MAX_PREPARED = 36  # forecasts the replay prepare step makes at most
ALERT_TIERS = ("HIGH", "CRITICAL")


def train_hours() -> float:
    return get_settings().forecast_train_hours


def data_end_for(at: datetime) -> datetime:
    """End of the newest bucket Snowflake has at `at`: the sync sends a bucket BUCKET_MARGIN after it closes."""
    t = at - BUCKET_MARGIN - timedelta(minutes=1)
    return t.replace(minute=t.minute - t.minute % 15, second=0, microsecond=0)


def _lit(ts: datetime) -> str:
    return f"''{naive_utc(ts):%Y-%m-%d %H:%M:%S}''::TIMESTAMP_NTZ"


def training_query(rid: int, column: str, start: datetime, end: datetime) -> str:
    """The SYSTEM$QUERY_REFERENCE body (quotes doubled) for one resident × metric over [start, end)."""
    return (
        f"SELECT BUCKET_TS, {column} FROM CORE.VITALS_15M WHERE RESIDENT_ID = {int(rid)} "
        f"AND BUCKET_TS >= {_lit(start)} AND BUCKET_TS < {_lit(end)} AND {column} IS NOT NULL"
    )


def create_sql(model: str, rid: int, column: str, start: datetime, end: datetime) -> str:
    return (
        f"CREATE OR REPLACE SNOWFLAKE.ML.FORECAST {model}("
        f"INPUT_DATA => SYSTEM$QUERY_REFERENCE('{training_query(rid, column, start, end)}'), "
        f"TIMESTAMP_COLNAME => 'BUCKET_TS', TARGET_COLNAME => '{column}', "
        "CONFIG_OBJECT => {'ON_ERROR': 'SKIP', 'evaluate': FALSE})"
    )


def forecast_sql(model: str) -> str:
    return (
        f"CALL {model}!FORECAST(FORECASTING_PERIODS => {PERIODS}, "
        f"CONFIG_OBJECT => {{'prediction_interval': {PREDICTION_INTERVAL}}})"
    )


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def _r(v) -> float | None:
    return None if v is None else round(float(v), 2)


def parse_forecast(cols: list[str], rows: list[tuple]) -> list[list]:
    """!FORECAST rows → [[ts_iso, forecast, lower, upper], …] in time order."""
    idx = {c.upper(): i for i, c in enumerate(cols)}
    out = [
        [_utc(r[idx["TS"]]).isoformat(), _r(r[idx["FORECAST"]]), _r(r[idx["LOWER_BOUND"]]), _r(r[idx["UPPER_BOUND"]])]
        for r in rows
    ]
    return sorted(out, key=lambda p: p[0])


def build_payload(
    made_at: datetime,
    data_end: datetime,
    hours: float,
    metrics: dict[str, dict],
    *,
    copd: bool,
    tz: ZoneInfo,
    seconds: float,
    trigger: str,
) -> dict:
    """The ai_insights payload: per metric the forecast points and the last observed bucket; the crossings, P_trend
    and callout (`trend.annotate`) are recomputed whenever the payload is read."""
    payload = {
        "as_of": made_at.isoformat(),
        "data_through": data_end.isoformat(),
        "train_hours": hours,
        "periods": PERIODS,
        "chart_periods": CHART_PERIODS,
        "interval": PREDICTION_INTERVAL,
        "model": "SNOWFLAKE.ML.FORECAST (single-series, per metric)",
        "copd": copd,
        "metrics": metrics,
        "seconds": round(seconds, 1),
        "trigger": trigger,
    }
    return trend.annotate(payload, tz, copd)


# --------------------------------------------------------------------------- Snowflake (blocking)


def run_models(sconn, rid: int, data_end: datetime, hours: float) -> tuple[dict[str, dict], dict[str, str]]:
    """Blocking: train + forecast the three metrics in parallel. → ({metric: result}, {metric: error})."""
    start = data_end - timedelta(hours=hours)

    def one(metric: str) -> tuple[str, dict | None, str | None]:
        column = METRICS[metric]
        try:
            n, last_ts, last_v = sf.query(
                sconn,
                f"SELECT COUNT(*), MAX(BUCKET_TS), MAX_BY({column}, BUCKET_TS) FROM CORE.VITALS_15M "
                f"WHERE RESIDENT_ID = %s AND BUCKET_TS >= %s AND BUCKET_TS < %s AND {column} IS NOT NULL",
                (rid, naive_utc(start), naive_utc(data_end)),
                feature="forecast.inputs",
            )[0]
            if n < MIN_ROWS:
                return metric, None, f"only {n} buckets in {hours:g} h"
            model = f"ML.FC_R{rid}_{metric.upper()}_{uuid.uuid4().hex[:8]}"
            sf.query(sconn, create_sql(model, rid, column, start, data_end), feature="forecast.train")
            try:
                cols, rows = sf.query_columns(sconn, forecast_sql(model), feature="forecast.predict")
            finally:
                try:
                    sf.query(sconn, f"DROP SNOWFLAKE.ML.FORECAST IF EXISTS {model}", feature="forecast.drop")
                except Exception as exc:  # noqa: BLE001 — a leftover model is harmless
                    log.info("forecast: could not drop %s: %s", model, exc)
            result = {
                "points": parse_forecast(cols, rows),
                "n_train": int(n),
                "last_ts": _utc(last_ts).isoformat(),
                "last_value": _r(last_v),
            }
            return metric, result, None
        except Exception as exc:  # one metric failing must not lose the others
            return metric, None, " ".join(str(exc).split())[:200]

    out, errors = {}, {}
    with ThreadPoolExecutor(max_workers=len(METRICS)) as pool:
        for metric, res, err in pool.map(one, METRICS):
            if res is not None:
                out[metric] = res
            elif err is not None:
                errors[metric] = err
    return out, errors


def results_frame(rid: int, made_at: datetime, metrics: dict[str, dict]) -> pd.DataFrame:
    rows = [
        (rid, metric, naive_utc(made_at), naive_utc(datetime.fromisoformat(p[0])), p[1], p[2], p[3])
        for metric, m in metrics.items()
        for p in m["points"]
    ]
    df = pd.DataFrame(rows, columns=["RESIDENT_ID", "METRIC", "AS_OF", "TS", "FORECAST", "LOWER_BOUND", "UPPER_BOUND"])
    for c in ("AS_OF", "TS"):
        df[c] = df[c].astype("datetime64[us]")
    df["CREATED_AT"] = pd.Series([naive_utc(datetime.now(UTC))] * len(df), dtype="datetime64[us]")
    return df


# --------------------------------------------------------------------------- one forecast


@dataclass
class ForecastStats:
    phase: str = "starting"  # starting | running | disabled | error
    requests: int = 0
    completed: int = 0
    errors: int = 0
    last_ms: float | None = None
    last_resident: int | None = None
    last_callout: str | None = None
    last_error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


stats = ForecastStats()


async def forecast_one(
    sconn,
    rid: int,
    asked_at: datetime,
    *,
    known_at: datetime | None = None,
    trigger: str,
    tz: ZoneInfo,
) -> dict | None:
    """Forecast resident `rid` from the data Snowflake had at `asked_at`; store it as known at `known_at` (live:
    when it finished; prepare: asked_at + REVEAL_DELAY). Returns the payload, or None without enough data.
    A pooled Tiger connection is held only for the reads/writes, never across the ~1–2 min Snowflake training."""
    t0 = time.perf_counter()
    async with tiger.pooled() as tconn:
        row = await tiger.fetchone(tconn, "SELECT flags FROM residents WHERE resident_id = %s", (rid,))
    copd = bool(row and "copd" in (row[0] or ()))
    data_end = data_end_for(asked_at)
    hours = train_hours()
    metrics, errors = await asyncio.to_thread(run_models, sconn, rid, data_end, hours)
    if errors:
        log.info("forecast: R-%d %s", rid, "; ".join(f"{m}: {e}" for m, e in errors.items()))
    if not metrics:
        return None
    seconds = time.perf_counter() - t0
    made_at = known_at or datetime.now(UTC).replace(microsecond=0)
    payload = build_payload(made_at, data_end, hours, metrics, copd=copd, tz=tz, seconds=seconds, trigger=trigger)
    if errors:
        payload["errors"] = errors
    async with tiger.pooled() as tconn:
        await tiger.upsert_insights(tconn, "forecast", [(rid, made_at, payload)])
    try:
        await asyncio.to_thread(
            lambda: sf.write_df(
                sconn, results_frame(rid, made_at, metrics), "FORECAST_RESULTS", feature="forecast.store"
            )
        )
    except Exception as exc:  # noqa: BLE001 — Tiger has it; the Snowflake copy is for Cortex Analyst / audit
        log.warning("forecast: storing R-%d in CORE.FORECAST_RESULTS failed: %s", rid, exc)
    return payload


# --------------------------------------------------------------------------- live triggers


class Forecaster:
    """Live forecasts: on an alert into HIGH/CRITICAL and every REFRESH while the resident stays there."""

    def __init__(self) -> None:
        self.tz = ZoneInfo(get_settings().facility_tz)
        self.enabled = False
        self.sf = sf.SharedConnection(warehouse=sf.ml_warehouse())
        self.last: dict[int, datetime] = {}  # rid → as_of of the newest forecast (or attempt)
        self.inflight: dict[int, asyncio.Task] = {}
        self._sem = asyncio.Semaphore(CONCURRENCY)

    def due(self, rid: int, at: datetime) -> bool:
        task = self.inflight.get(rid)
        if task is not None and not task.done():
            return False
        last = self.last.get(rid)
        return last is None or at - last >= REFRESH

    def on_alert(self, rid: int, tier: str) -> asyncio.Task | None:
        from app.state import sim_state

        if not self.enabled or sim_state.mode != "live" or tier not in ALERT_TIERS:
            return None
        now = datetime.now(UTC)
        if not self.due(rid, now):
            return None
        return self.request(rid, "alert")

    def request(self, rid: int, trigger: str) -> asyncio.Task:
        self.last[rid] = datetime.now(UTC)  # an attempt counts, so a failing Snowflake isn't hammered
        stats.requests += 1
        task = asyncio.create_task(self._run(rid, trigger), name=f"forecast-{rid}")
        self.inflight[rid] = task
        task.add_done_callback(lambda t, r=rid: self.inflight.pop(r, None) if self.inflight.get(r) is t else None)
        return task

    async def _run(self, rid: int, trigger: str) -> dict | None:
        async with self._sem:
            t0 = time.perf_counter()
            try:
                payload = await self._forecast(rid, trigger)
            except Exception as exc:
                stats.errors += 1
                stats.last_error = f"R-{rid}: {' '.join(str(exc).split())[:200] or type(exc).__name__}"
                log.warning("forecast: %s", stats.last_error)
                return None
            stats.completed += 1
            stats.last_ms, stats.last_resident = round((time.perf_counter() - t0) * 1000), rid
            if payload:
                stats.last_callout = payload.get("callout")
                await hub.broadcast({"type": "insight", "kind": "forecast", "resident_id": rid,
                                     "as_of": payload["as_of"], "live": True, "callout": payload.get("callout")})  # fmt: skip
                log.info("forecast: R-%d (%s) in %.0f s · %s", rid, trigger, payload["seconds"],
                         payload.get("callout") or "no threshold crossing")  # fmt: skip
            return payload

    async def _forecast(self, rid: int, trigger: str) -> dict | None:
        sconn = await asyncio.to_thread(self.sf.get)
        try:
            return await forecast_one(sconn, rid, datetime.now(UTC), trigger=trigger, tz=self.tz)
        except Exception:
            await asyncio.to_thread(self.sf.reset)
            raise

    def scan(self, items: list[tuple[int, str]], at: datetime) -> list[int]:
        """(resident_id, tier) of the live queue → residents to (re)forecast now."""
        return [rid for rid, tier in items if tier in ALERT_TIERS and self.due(rid, at)]

    async def run(self) -> None:
        from app.scoring import evaluator as ev
        from app.state import sim_state

        try:
            async with tiger.pooled() as conn:
                for rid, as_of in await tiger.fetchall(
                    conn,
                    """SELECT resident_id, max(as_of) FROM ai_insights
                       WHERE kind = 'forecast' AND as_of <= now() GROUP BY resident_id""",
                ):
                    self.last[rid] = as_of
        except Exception as exc:  # noqa: BLE001 — start without history; at worst one extra forecast each
            log.warning("forecast: could not load history: %s", exc)
        stats.phase = "running"
        while True:
            await asyncio.sleep(SCAN_S)
            queue = ev.evaluator.latest if ev.evaluator is not None else None
            if queue is None or sim_state.mode != "live" or sim_state.paused:
                continue
            for rid in self.scan([(it.profile.resident_id, it.tier) for it in queue.items], queue.as_of):
                self.request(rid, "refresh" if rid in self.last else "tier_high")


forecaster: Forecaster | None = None


def get_forecaster() -> Forecaster:
    global forecaster
    if forecaster is None:
        forecaster = Forecaster()
    return forecaster


def start() -> asyncio.Task | None:
    s = get_settings()
    f = get_forecaster()
    if not (s.tiger_configured and s.snowflake_configured and s.forecast_enabled):
        stats.phase = "disabled"
        return None
    f.enabled = True
    return asyncio.create_task(f.run(), name="forecaster")


# --------------------------------------------------------------------------- replay prepare


def plan_replay(transitions: dict[int, list[list]], night_end: datetime, cast: set[int] = frozenset(),
                cap: int = MAX_PREPARED) -> list[tuple[int, datetime]]:  # fmt: skip
    """(resident, asked_at) for last night, as the live Forecaster would ask: whenever the resident is HIGH/CRITICAL
    and their last forecast is REFRESH old (so on entry, then every REFRESH while it lasts). `transitions` = rid →
    [[ts_iso, tier, score], …] at each tier change (the gated pass's). Over `cap`, the cast is kept first, then the
    earliest asks."""
    plan: list[tuple[int, datetime]] = []
    for rid, changes in transitions.items():
        rid, last = int(rid), None
        for i, (ts, tier, _score) in enumerate(changes):
            if tier not in ALERT_TIERS:
                continue
            start = datetime.fromisoformat(ts)
            end = datetime.fromisoformat(changes[i + 1][0]) if i + 1 < len(changes) else night_end
            t = start if last is None else max(start, last + REFRESH)
            while t < end:
                plan.append((rid, t))
                last = t
                t += REFRESH
    if len(plan) > cap:
        plan = sorted(plan, key=lambda p: (p[0] not in cast, p[1]))[:cap]
    return sorted(plan, key=lambda p: (p[1], p[0]))


async def prepare_replay_forecasts(
    conn: psycopg.AsyncConnection,
    requests: list[tuple[int, datetime]],
    *,
    force: bool = False,
    say=log.info,
    on_done=None,
) -> dict:
    """Forecasts for (resident_id, asked_at) pairs, stored as known at asked_at + REVEAL_DELAY. Ones already cached
    for that moment are kept unless `force`."""
    tz = ZoneInfo(get_settings().facility_tz)
    if not requests:
        return {"status": "nothing to forecast", "requested": 0, "stored": 0, "cached": 0, "failed": 0}
    known = [(rid, asked + REVEAL_DELAY) for rid, asked in requests]
    existing = {
        (r["resident_id"], r["as_of"])
        for r in await tiger.fetch_dicts(
            conn,
            "SELECT resident_id, as_of FROM ai_insights WHERE kind = 'forecast' AND as_of >= %s AND as_of <= %s",
            (min(k for _, k in known), max(k for _, k in known)),
        )
    }
    todo = [(rid, asked) for rid, asked in requests if force or (rid, asked + REVEAL_DELAY) not in existing]
    say(f"  forecasts    {len(requests)} planned · {len(requests) - len(todo)} cached · running {len(todo)} "
        f"({PREPARE_CONCURRENCY} residents at a time, 3 single-series models each) …")  # fmt: skip
    t0 = time.perf_counter()
    stored = failed = 0
    callouts: list[dict] = []
    sem = asyncio.Semaphore(PREPARE_CONCURRENCY)
    sconn = await asyncio.to_thread(sf.connect, None, sf.ml_warehouse()) if todo else None

    async def one(rid: int, asked: datetime) -> None:
        try:
            await _one(rid, asked)
        finally:
            if on_done is not None:
                on_done()

    async def _one(rid: int, asked: datetime) -> None:
        nonlocal stored, failed
        async with sem:
            try:
                p = await forecast_one(sconn, rid, asked, known_at=asked + REVEAL_DELAY, trigger="prepare", tz=tz)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                say(f"  forecast     R-{rid} {asked.astimezone(tz):%H:%M}: failed {' '.join(str(exc).split())[:120]}")
                return
            if p is None:
                failed += 1
                return
            stored += 1
            if p.get("callout"):
                callouts.append({"resident_id": rid, "as_of": p["as_of"], "callout": p["callout"],
                                 "p_trend": p["p_trend"]})  # fmt: skip
            say(f"  forecast     R-{rid} asked {asked.astimezone(tz):%H:%M} · {p['seconds']:.0f} s · "
                f"{p.get('callout') or 'no NEWS2 3-point crossing within 4 h'}")  # fmt: skip

    try:
        await asyncio.gather(*(one(rid, asked) for rid, asked in todo))
    finally:
        if sconn is not None:
            await asyncio.to_thread(sconn.close)
    return {
        "status": "done",
        "requested": len(requests),
        "stored": stored,
        "cached": len(requests) - len(todo),
        "failed": failed,
        "seconds": round(time.perf_counter() - t0, 1),
        "train_hours": train_hours(),
        "callouts": sorted(callouts, key=lambda c: c["as_of"]),
    }
