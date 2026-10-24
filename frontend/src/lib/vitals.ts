// Pure helpers for the resident charts (SPEC §11.2): ranges, personal-baseline bands, NEWS2 zones.
import { facilityHour } from "@/lib/format";
import type { VitalBaselines } from "@/lib/types";

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

export function hasData(values: (number | null)[] | undefined): boolean {
  return !!values && values.some((v) => v !== null);
}

function round(v: number, digits: number): number {
  const f = 10 ** digits;
  return Math.round(v * f) / f;
}
