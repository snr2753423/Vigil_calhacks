// Pure helpers for the resident charts (SPEC §11.2): ranges, personal-baseline bands, NEWS2 zones.
import { facilityHour } from "@/lib/format";
import type { AnomalyBucket, AnomalyMetricKey, VitalBaselines } from "@/lib/types";

export type RangeKey = "6h" | "24h" | "7d";

export const RANGES: Record<RangeKey, { label: string; ms: number; res: "1m" | "15m" | "1h"; refreshMs: number }> = {
  "6h": { label: "6 h", ms: 6 * 3600_000, res: "1m", refreshMs: 30_000 },
  "24h": { label: "24 h", ms: 24 * 3600_000, res: "15m", refreshMs: 120_000 },
  "7d": { label: "7 d", ms: 7 * 86400_000, res: "1h", refreshMs: 600_000 },
};

/** 4-hour time-of-day block of facility local time (0 = 00:00–04:00), as the Tiger baseline job uses. */
export function todBlock(epochMs: number): number {
  return Math.floor(facilityHour(epochMs) / 4);
}

export type Band = { lower: (number | null)[]; width: (number | null)[]; p50: (number | null)[] };

/** The personal p05–p95 band at each timestamp (by its time-of-day block), as stacked lower + width. */
export function baselineBand(tsMs: number[], baselines: VitalBaselines, metric: string): Band {
  const blocks = baselines[metric] ?? {};
  const lower: (number | null)[] = [];
  const width: (number | null)[] = [];
  const p50: (number | null)[] = [];
  for (const t of tsMs) {
    const b = blocks[String(todBlock(t))];
    lower.push(b ? round(b.p05, 2) : null);
    width.push(b ? round(b.p95 - b.p05, 2) : null);
    p50.push(b ? round(b.p50, 2) : null);
  }
  return { lower, width, p50 };
}

/** value − personal median for the same block (skin temp is shown as change vs baseline, never absolute). */
export function deltaVsBaseline(
  tsMs: number[],
  values: (number | null)[],
  baselines: VitalBaselines,
  metric: string,
): (number | null)[] {
  const { p50 } = baselineBand(tsMs, baselines, metric);
  return values.map((v, i) => (v === null || p50[i] === null ? null : round(v - (p50[i] as number), 2)));
}

export type Zone = { from: number; to: number; points: 1 | 2 | 3 };

/** SpO₂ ranges that score NEWS2 points (SPEC §7b). Scale 2 (COPD, target 88–92) on room air. */
export function spo2Zones(scale: 1 | 2): Zone[] {
  return scale === 2
    ? [
        { from: 70, to: 83.5, points: 3 },
        { from: 83.5, to: 85.5, points: 2 },
        { from: 85.5, to: 87.5, points: 1 },
      ]
    : [
        { from: 70, to: 91.5, points: 3 },
        { from: 91.5, to: 93.5, points: 2 },
        { from: 93.5, to: 95.5, points: 1 },
      ];
}

export const BUCKET_MS = 15 * 60_000;

/** Snowflake's expected range at each chart timestamp: the mean bounds of the 15-min buckets that start
 *  inside [t, t + stepMs) (one bucket at 1m/15m resolution, four at 1h). `scale` converts sums (movement
 *  per 15 min → per chart step). Null where the model has no result. */
export function expectedBand(
  tsMs: number[],
  stepMs: number,
  buckets: AnomalyBucket[],
  metric: AnomalyMetricKey,
  scale = 1,
): Band {
  const starts = buckets
    .map((b) => ({ t: Date.parse(b.bucket), m: b.metrics[metric] }))
    .filter((b) => b.m && b.m.lower !== null && b.m.upper !== null)
    .sort((a, b) => a.t - b.t);
  const lower: (number | null)[] = [];
  const width: (number | null)[] = [];
  const p50: (number | null)[] = [];
  let j = 0;
  for (const t of tsMs) {
    // the bucket containing t (finer than 15 min) or every bucket starting in [t, t + step) (coarser)
    const lo = stepMs < BUCKET_MS ? t - BUCKET_MS + 1 : t;
    const hi = stepMs < BUCKET_MS ? t + 1 : t + stepMs;
    while (j < starts.length && starts[j].t < lo) j++;
    let n = 0;
    let l = 0;
    let u = 0;
    let f = 0;
    for (let k = j; k < starts.length && starts[k].t < hi; k++) {
      const m = starts[k].m!;
      l += m.lower!;
      u += m.upper!;
      f += m.forecast ?? (m.lower! + m.upper!) / 2;
      n++;
    }
    lower.push(n ? round((l / n) * scale, 2) : null);
    width.push(n ? round(((u - l) / n) * scale, 2) : null);
    p50.push(n ? round((f / n) * scale, 2) : null);
  }
  return { lower, width, p50 };
}

export type AnomalyPoint = { t: number; value: number; severity: number; bucket: string; y: number | null };

/** Anomaly dots on the chart's own line: one per flagged bucket, at the chart value of the step that holds
 *  the bucket's midpoint (so it sits on the line at any resolution or transform). */
export function anomalyPoints(
  tsMs: number[],
  values: (number | null)[],
  buckets: AnomalyBucket[],
  metric: AnomalyMetricKey,
): AnomalyPoint[] {
  if (tsMs.length < 2) return [];
  const step = tsMs[1] - tsMs[0];
  const out: AnomalyPoint[] = [];
  for (const b of buckets) {
    const m = b.metrics[metric];
    if (!m || m.severity <= 0) continue;
    const mid = Date.parse(b.bucket) + BUCKET_MS / 2;
    const i = Math.floor((mid - tsMs[0]) / step);
    if (i < 0 || i >= tsMs.length) continue;
    // nearest step with a value (a 1-min series can have gaps)
    let k = -1;
    for (let d = 0; d <= Math.max(1, Math.round(BUCKET_MS / step)) && k < 0; d++) {
      if (values[i + d] != null) k = i + d;
      else if (values[i - d] != null) k = i - d;
    }
    if (k < 0) continue;
    out.push({ t: tsMs[k], value: values[k] as number, severity: m.severity, bucket: b.bucket, y: m.y });
  }
  return out;
}

export function hasData(values: (number | null)[] | undefined): boolean {
  return !!values && values.some((v) => v !== null);
}

function round(v: number, digits: number): number {
  const f = 10 ** digits;
  return Math.round(v * f) / f;
}

/** Live device readings (e.g. the Polar band, 1 s) newer than `after` appended to a bucketed series, so a
 *  live resident's chart moves within a second instead of at the next 30-s reload. */
export function appendLive(
  ts: number[],
  values: (number | null)[],
  live: [number, number][] | undefined,
  after: number,
): { ts: number[]; values: (number | null)[] } {
  const fresh = (live ?? []).filter(([t]) => t > after && t > (ts[ts.length - 1] ?? -Infinity));
  if (fresh.length === 0) return { ts, values };
  return { ts: [...ts, ...fresh.map(([t]) => t)], values: [...values, ...fresh.map(([, v]) => v)] };
}
