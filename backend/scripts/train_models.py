"""`make train`: train the 5 multi-series SNOWFLAKE.ML.ANOMALY_DETECTION models (SPEC §9 ②).

Window: up to ANOMALY_TRAIN_DAYS (default 12) days of CORE.VITALS_15M ending where last night's holdout
starts (noon before the scripted night), so the replayed night is out-of-sample. SPEC asks for days
−14 … −2; a shorter seed is clamped to what exists (a 3-day seed trains on ~2 days). The five models
train in parallel and the time of each is printed. Afterwards every synced bucket since the end of the
training data is run through DETECT_ANOMALIES (skip with --no-detect), so the fast tier and the charts
have results at once, and the cast's flags for last night are summarized.
"""

import argparse
import asyncio
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from detect_anomalies import last_night, summarize

from app.config import get_settings
from app.db import snowflake_sql as sf
from app.db import tiger
from app.deep import anomaly, sync

MODELS_SQL = sf.MIGRATIONS_DIR / "004_models.sql"
RECOMMENDED_DAYS = 7
_MODEL_STMT = re.compile(r"CREATE OR REPLACE SNOWFLAKE\.ML\.ANOMALY_DETECTION ML\.(\w+)", re.I)
METRIC_BY_MODEL = {model: metric for metric, (model, _) in anomaly.MODELS.items()}


def train(sconn, train_from: datetime, train_to: datetime) -> dict[str, dict]:
    """Blocking. Run 004_models.sql: the window table and views first, then the window row, then the five
    model statements in parallel. Returns per metric: model, seconds, rows, series."""
    statements = sf.script_statements(MODELS_SQL)
    setup = [s for s in statements if not _MODEL_STMT.search(s)]
    models = {_MODEL_STMT.search(s).group(1): s for s in statements if _MODEL_STMT.search(s)}
    for stmt in setup:
        sf.query(sconn, stmt, feature="anomaly.train")
    sf.query(
        sconn,
        """MERGE INTO ML.TRAINING_WINDOW w USING (SELECT 'anomaly' AS NAME) s ON w.NAME = s.NAME
           WHEN MATCHED THEN UPDATE SET TRAIN_FROM = %(f)s, TRAIN_TO = %(t)s, UPDATED_AT = SYSDATE()
           WHEN NOT MATCHED THEN INSERT (NAME, TRAIN_FROM, TRAIN_TO, UPDATED_AT)
                                 VALUES ('anomaly', %(f)s, %(t)s, SYSDATE())""",
        {"f": sync.naive_utc(train_from), "t": sync.naive_utc(train_to)},
        feature="anomaly.train",
    )
    sizes = {}
    for model in models:
        metric = METRIC_BY_MODEL[model]
        ((rows, series),) = sf.query(
            sconn, f"SELECT count(*), count(DISTINCT RESIDENT_ID) FROM ML.AD_TRAIN_{model[3:]}", feature="anomaly.train"
        )
        sizes[metric] = (rows, series)

    def one(model: str) -> tuple[str, float]:
        t0 = time.perf_counter()
        sf.query(sconn, models[model], feature=f"anomaly.train.{METRIC_BY_MODEL[model]}")
        secs = time.perf_counter() - t0
        print(f"  ✓ ML.{model:<13} {secs:6.1f}s  ({sizes[METRIC_BY_MODEL[model]][0]:,} rows)", flush=True)
        return model, secs

    with ThreadPoolExecutor(max_workers=len(models)) as pool:
        timings = dict(pool.map(one, models))
    return {
        METRIC_BY_MODEL[m]: {
            "model": f"ML.{m}",
            "seconds": round(s, 1),
            "rows": sizes[METRIC_BY_MODEL[m]][0],
            "series": sizes[METRIC_BY_MODEL[m]][1],
        }
        for m, s in timings.items()
    }


async def run(days: float, detect: bool) -> None:
    async with tiger.connect(application_name="vigil-train") as conn:
        seed = await tiger.get_meta(conn, "seed")
        holdout = await tiger.get_meta(conn, "baseline_holdout")
        state = await tiger.get_sync_state(conn)
        if not seed or not holdout:
            raise SystemExit("train: no seed metadata — run `make seed` first")
        if sync.VITALS_WM not in state:
            raise SystemExit("train: Snowflake has no vitals yet — run `make sf-load` (or `make seed`)")

        sconn = await asyncio.to_thread(sf.connect)
        try:
            ((first,),) = await asyncio.to_thread(sf.query, sconn, "SELECT min(BUCKET_TS) FROM CORE.VITALS_15M")
            if first is None:
                raise SystemExit("train: CORE.VITALS_15M is empty — run `make sf-load`")
            train_to = datetime.fromisoformat(holdout["start"])
            train_from = max(first.replace(tzinfo=UTC), train_to - timedelta(days=days))
            span_days = (train_to - train_from).total_seconds() / 86400
            print(f"train: window {train_from:%Y-%m-%d %H:%M} → {train_to:%Y-%m-%d %H:%M} UTC "
                  f"({span_days:.1f} days; last night held out), 5 models in parallel …")  # fmt: skip
            if wh := get_settings().snowflake_train_warehouse:
                await asyncio.to_thread(sf.query, sconn, f"USE WAREHOUSE {wh}", feature="anomaly.train")
                print(f"train: using warehouse {wh}")
            t0 = time.perf_counter()
            models = await asyncio.to_thread(train, sconn, train_from, train_to)
            if wh:
                await asyncio.to_thread(sf.query, sconn, f"USE WAREHOUSE {get_settings().snowflake_warehouse}")
            total = time.perf_counter() - t0
            print(f"train: done in {total:.1f}s")
            await tiger.set_meta(
                conn,
                anomaly.META_KEY,
                {
                    "trained_at": datetime.now(UTC).isoformat(),
                    "seeded_at": seed["seeded_at"],
                    "train_from": train_from.isoformat(),
                    "train_to": train_to.isoformat(),
                    "days": round(span_days, 2),
                    "seconds": round(total, 1),
                    "prediction_interval": anomaly.PREDICTION_INTERVAL,
                    "models": models,
                },
            )
            if span_days < RECOMMENDED_DAYS:
                print(f"train: note — only {span_days:.1f} days of history. Each resident's normal (and its "
                      f"night-to-night variation) is learned from {span_days:.0f} nights; re-seed with "
                      f"`make seed DAYS=14` for the SPEC's 12-day window.")  # fmt: skip

            if detect:
                if not await tiger.try_lock_sync(conn):
                    print("train: waiting for the backend's sync cycle to finish …")
                    await tiger.lock_sync(conn)
                try:
                    end = (await tiger.get_sync_state(conn))[sync.VITALS_WM]["watermark"] + sync.BUCKET
                    print(f"detect: {train_to:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} UTC (all residents) …")
                    det = await anomaly.detect_range(conn, sconn, train_to, end, train_to)
                    if det.ok:
                        await tiger.set_sync_state(conn, anomaly.ANOMALY_WM, end - sync.BUCKET, len(det.results))
                finally:
                    await tiger.unlock_sync(conn)
                print(f"detect: {len(det.results):,} results in {anomaly.stats.last_seconds:.1f}s · per model " +
                      ", ".join(f"{m} {s:.1f}s" for m, s in det.seconds.items()))  # fmt: skip
                for m, err in det.errors.items():
                    print(f"  ✗ {m}: {err}")
                await summarize(conn, *await last_night(conn))
        finally:
            sconn.close()


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=float, default=settings.anomaly_train_days, help="max training days")
    parser.add_argument("--no-detect", action="store_true", help="train only")
    args = parser.parse_args()
    if not (settings.snowflake_configured and settings.tiger_configured):
        print("train: Tiger and Snowflake must both be configured in .env", file=sys.stderr)
        return 1
    asyncio.run(run(args.days, not args.no_detect))
    return 0


if __name__ == "__main__":
    sys.exit(main())
