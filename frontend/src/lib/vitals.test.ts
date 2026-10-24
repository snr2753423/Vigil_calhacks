import { describe, expect, it } from "vitest";

import { offsetForDisplayTime } from "@/lib/display-clock";
import { describeEvent } from "@/lib/events";
import { formatMinutes } from "@/lib/format";
import type { AnomalyBucket, Forecast } from "@/lib/types";
import { anomalyPoints, appendLive, baselineBand, deltaVsBaseline, expectedBand, forecastOverlay, latestForecast, spo2Zones, todBlock } from "@/lib/vitals";

const baselines = {
  hr: { "0": { p05: 55, p50: 60, p95: 70 }, "3": { p05: 65, p50: 72, p95: 85 } },
  skin_temp: { "0": { p05: 33.8, p50: 34.0, p95: 34.3 } },
};

describe("baseline bands", () => {
  it("uses the facility-time 4-hour block of each timestamp", () => {
    const night = Date.parse("2026-10-16T09:14:00Z"); // 02:14 PDT → block 0
    const afternoon = Date.parse("2026-10-16T21:00:00Z"); // 14:00 PDT → block 3
    const evening = Date.parse("2026-10-17T02:00:00Z"); // 19:00 PDT → block 4 (no stats)
    expect([todBlock(night), todBlock(afternoon), todBlock(evening)]).toEqual([0, 3, 4]);
    const b = baselineBand([night, afternoon, evening], baselines, "hr");
    expect(b.lower).toEqual([55, 65, null]);
    expect(b.width).toEqual([15, 20, null]);
    expect(deltaVsBaseline([night, night], [34.8, null], baselines, "skin_temp")).toEqual([0.8, null]);
  });

  it("shades the NEWS2 SpO₂ bands for scale 1 and scale 2", () => {
    expect(spo2Zones(1).map((z) => [z.to, z.points])).toEqual([[91.5, 3], [93.5, 2], [95.5, 1]]);
    expect(spo2Zones(2).map((z) => [z.to, z.points])).toEqual([[83.5, 3], [85.5, 2], [87.5, 1]]);
  });
});

describe("display clock offset", () => {
  it("makes the facility clock read the chosen time, within ±12 h", () => {
    const now = Date.parse("2026-10-16T22:00:00Z"); // 15:00 PDT
    expect(offsetForDisplayTime("02:14", now)).toBe(11 * 3600 + 14 * 60);
    expect(offsetForDisplayTime("14:00", now)).toBe(-3600);
    expect(() => offsetForDisplayTime("2pm", now)).toThrow();
  });
});

describe("formatting", () => {
  it("formats minutes and events in plain language", () => {
    expect([formatMinutes(5), formatMinutes(125), formatMinutes(120), formatMinutes(4000)]).toEqual([
      "5 min",
      "2 h 05 min",
      "2 h",
      "3 d",
    ]);
    expect(describeEvent({ ts: "", type: "spot_check", staff_id: 1, payload: { sbp: 104, dbp: 58, temp_c: 38.4 } }).detail).toBe(
      "BP 104/58 · Temp 38.4 °C",
    );
    expect(describeEvent({ ts: "", type: "check_logged", staff_id: 1, payload: { kind: "round", round: "02:00" } }).title).toBe(
      "Round check (02:00)",
    );
  });
});

const T0 = Date.parse("2026-10-16T09:00:00Z");
const MIN = 60_000;
const bucket = (startMin: number, lower: number, upper: number, severity = 0, y = 70): AnomalyBucket => ({
  as_of: new Date(T0 + (startMin + 20) * MIN).toISOString(),
  bucket: new Date(T0 + startMin * MIN).toISOString(),
  metrics: { hr: { y, forecast: (lower + upper) / 2, lower, upper, z: severity ? 4 : 0, severity, is_anomaly: severity > 0 } },
  anomalous: severity ? ["hr"] : [],
  p_anom: severity,
});

describe("Snowflake overlays", () => {
  const buckets = [bucket(0, 60, 70), bucket(15, 62, 72, 0.6, 85), bucket(30, 64, 74), bucket(45, 66, 76)];

  it("maps each 1-min step to the bucket that contains it", () => {
    const ts = [0, 14, 15, 29, 70].map((m) => T0 + m * MIN);
    const b = expectedBand(ts, MIN, buckets, "hr");
    expect(b.lower).toEqual([60, 60, 62, 62, null]);
    expect(b.width).toEqual([10, 10, 10, 10, null]);
  });

  it("averages the four buckets of an hour at 1-h resolution and scales sums", () => {
    const b = expectedBand([T0], 60 * MIN, buckets, "hr", 2);
    expect(b.lower).toEqual([126]); // mean lower 63 × 2
    expect(b.width).toEqual([20]);
  });

  it("puts a dot on the line for each flagged bucket only", () => {
    const ts = Array.from({ length: 60 }, (_, i) => T0 + i * MIN);
    const values: (number | null)[] = ts.map((_, i) => 60 + i);
    values[22] = null; // gap at the bucket's midpoint (09:22:30 → step 22): the nearest value is used
    const pts = anomalyPoints(ts, values, buckets, "hr");
    expect(pts).toHaveLength(1);
    expect(pts[0].t).toBe(T0 + 23 * MIN);
    expect(pts[0].value).toBe(83);
    expect(pts[0].severity).toBe(0.6);
    expect(anomalyPoints(ts, values, buckets, "rr")).toEqual([]);
  });
});

describe("appendLive", () => {
  it("appends readings newer than the fetch and the last bucket", () => {
    const out = appendLive([0, 60_000], [70, 71], [[30_000, 1], [61_000, 72], [62_000, 73]], 50_000);
    expect(out).toEqual({ ts: [0, 60_000, 61_000, 62_000], values: [70, 71, 72, 73] });
  });

  it("returns the series unchanged without fresh readings", () => {
    const ts = [0, 60_000];
    const values = [70, null];
    expect(appendLive(ts, values, undefined, 0)).toEqual({ ts, values });
    expect(appendLive(ts, values, [[10, 80]], 100)).toEqual({ ts, values });
  });
});

describe("Snowflake forecast overlay", () => {
  const fc = (asOf: string): Forecast => ({
    as_of: asOf, data_through: "2026-10-17T08:00:00+00:00", train_hours: 12, periods: 16, chart_periods: 2,
    interval: 0.9, model: "SNOWFLAKE.ML.FORECAST", p_trend: 0.5, callout: "RR forecast to cross 25 by ~04:30", seconds: 80,
    metrics: {
      rr: {
        points: [["2026-10-17T08:00:00+00:00", 20.7, 20.4, 21.0], ["2026-10-17T08:15:00+00:00", 21.0, 20.5, 21.6],
                 ["2026-10-17T08:30:00+00:00", 21.4, 20.6, 22.2]],
        n_train: 18, last_ts: "2026-10-17T07:45:00+00:00", last_value: 20.3,
        crossing: { threshold: 25, direction: "up", bucket: "2026-10-17T11:15:00+00:00", crosses_at: "2026-10-17T11:30:00+00:00" },
      },
    },
  });

  it("starts at the last reading and keeps the chart's periods", () => {
    const o = forecastOverlay(fc("2026-10-17T08:10:00Z"), "rr")!;
    expect(o.value).toEqual([20.3, 20.7, 21.0]); // last observed + 2 chart periods
    expect(o.ts[0]).toBe(Date.parse("2026-10-17T07:52:30Z")); // bucket centre
    expect(o.threshold).toBe(25);
    expect(forecastOverlay(fc("2026-10-17T08:10:00Z"), "hr")).toBeNull();
  });

  it("picks the newest forecast known and still fresh", () => {
    const items = [fc("2026-10-17T08:10:00Z"), fc("2026-10-17T08:40:00Z")];
    expect(latestForecast(items, Date.parse("2026-10-17T08:30:00Z"))?.as_of).toBe("2026-10-17T08:10:00Z");
    expect(latestForecast(items, Date.parse("2026-10-17T09:00:00Z"))?.as_of).toBe("2026-10-17T08:40:00Z");
    expect(latestForecast(items, Date.parse("2026-10-17T10:00:00Z"))).toBeNull(); // > 60 min old
  });
});
