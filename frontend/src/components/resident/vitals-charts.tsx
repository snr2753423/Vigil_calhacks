"use client";

import { Loader2, TrendingUp } from "lucide-react";
import { useMemo, useState } from "react";
import { cn } from "cn";

import { ChartPanel, LegendSwatch } from "@/components/resident/chart-panel";
import { bandedLineOption, deltaOption, inBedOption, motionOption, PALETTES, spo2Option } from "@/lib/chart-options";
import { useTheme } from "@/hooks/use-theme";
import type { Forecast, VitalBaselines } from "@/lib/types";
import { useVitalsWindow } from "@/hooks/use-vitals";
import { useNow } from "@/hooks/use-now";
import { facilityHHMM } from "@/lib/format";
import { useLiveStore } from "@/store/live";
import {
  anomalyPoints,
  appendLive,
  BUCKET_MS,
  baselineBand,
  deltaVsBaseline,
  expectedBand,
  forecastOverlay,
  hasData,
  latestForecast,
  RANGES,
  type RangeKey,
} from "@/lib/vitals";

const METRICS = "hr,rr,spo2,skin_temp,motion,in_bed";
// During a replay the charts end at the cursor and reload as it moves, in steps of this much replay time.
const REPLAY_RELOAD_STEP_MS: Record<RangeKey, number> = { "6h": 4 * 60_000, "24h": 15 * 60_000, "7d": 60 * 60_000 };

type Props = { residentId: number; baselines: VitalBaselines; copd: boolean; pronoun: string };

/** HR / RR / SpO₂ / skin-temp change / movement / bed presence from Tiger's continuous aggregates
 * (vitals_1m for 6 h, vitals_15m for 24 h, vitals_1h for 7 d), with a shared crosshair. */
export function VitalsCharts({ residentId, baselines, copd, pronoun }: Props) {
  const [range, setRange] = useState<RangeKey>("6h");
  const liveHr = useLiveStore((s) => s.liveHr[residentId]);
  const polar = useLiveStore((s) => s.devices[`${residentId}:polar`]);
  const now = useNow();
  const lastInsight = useLiveStore((s) => s.lastInsight);
  const forecastTick = lastInsight?.kind === "forecast" && lastInsight.resident_id === residentId ? lastInsight.as_of : null;
  const { theme } = useTheme();
  const pal = PALETTES[theme];
  // During a replay the charts end at the cursor and reload as it moves (REPLAY_RELOAD_STEP_MS).
  const { data, fetchedAt, anomalies, forecasts, error, loading, cursor, replayEnd } = useVitalsWindow({
    residentId,
    metrics: METRICS,
    spanMs: RANGES[range].ms,
    res: RANGES[range].res,
    stepMs: REPLAY_RELOAD_STEP_MS[range],
    refreshMs: RANGES[range].refreshMs,
    forecasts: true,
    reloadKey: forecastTick,
  });

  const charts = useMemo(() => {
    if (!data || data.ts.length < 2) return null;
    const ts = data.ts.map((t) => Date.parse(t));
    const s = data.series;
    const longRange = range !== "6h";
    const rrBand = baselineBand(ts, baselines, "rr");
    const skinDelta = s.skin_temp ? deltaVsBaseline(ts, s.skin_temp, baselines, "skin_temp") : [];
    // Snowflake overlays: the model's expected range (99 % interval) and the buckets it flagged.
    const step = ts[1] - ts[0];
    const a = anomalies;
    const exp = (m: "hr" | "rr" | "spo2" | "skin_temp") => (a.length ? expectedBand(ts, step, a, m) : undefined);
    const skinExp = exp("skin_temp");
    if (skinExp) {
      const p50 = baselineBand(ts, baselines, "skin_temp").p50; // shown as change vs usual, like the line
      skinExp.lower = skinExp.lower.map((v, i) => (v === null || p50[i] === null ? null : v - (p50[i] as number)));
    }
    const dots = (m: "hr" | "rr" | "spo2" | "skin_temp" | "motion", v: (number | null)[] | undefined) =>
      v && a.length ? anomalyPoints(ts, v, a, m) : [];
    const flagged = a.filter((b) => b.anomalous.length > 0).length;
    // Snowflake FORECAST: the newest fresh one, drawn past the last reading on HR / RR / SpO₂ (not on 7 d).
    const endMs = replayEnd !== null ? Math.min(replayEnd, cursor ?? replayEnd) : fetchedAt; // live: the last fetch
    const fc = range === "7d" ? null : latestForecast(forecasts, endMs);
    const fo = (m: "hr" | "rr" | "spo2") => (fc ? (forecastOverlay(fc, m) ?? undefined) : undefined);
    const fHr = fo("hr");
    const fRr = fo("rr");
    const fSpo2 = fo("spo2");
    const xMax = Math.max(0, ...[fHr, fRr, fSpo2].map((f) => (f ? f.ts[f.ts.length - 1] : 0)));
    // A live device (the presenter's Polar band) adds its 1-s readings after the last fetch.
    const hr = longRange || !s.hr || replayEnd !== null ? { ts, values: s.hr ?? [] } : appendLive(ts, s.hr, liveHr, fetchedAt);
    const hrBand = baselineBand(hr.ts, baselines, "hr");
    const hrExp = a.length ? expectedBand(hr.ts, step, a, "hr") : undefined;
    return {
      hr: hasData(hr.values)
        ? bandedLineOption({ theme, ts: hr.ts, values: hr.values, band: hrBand, name: "Heart rate", unit: " bpm", digits: 0, longRange, height: 200, expected: hrExp, anomalies: dots("hr", s.hr), forecast: fHr, xMax })
        : null,
      rr: hasData(s.rr)
        ? bandedLineOption({ theme, ts, values: s.rr!, band: rrBand, name: "Resp. rate", unit: "/min", digits: 1, longRange, height: 150, expected: exp("rr"), anomalies: dots("rr", s.rr), forecast: fRr, xMax })
        : null,
      spo2: hasData(s.spo2)
        ? spo2Option({ theme, ts, values: s.spo2!, scale: copd ? 2 : 1, longRange, height: 150, expected: exp("spo2"), anomalies: dots("spo2", s.spo2), forecast: fSpo2, xMax })
        : null,
      skin: hasData(skinDelta)
        ? deltaOption({ theme, ts, values: skinDelta, longRange, height: 130, expected: skinExp, anomalies: dots("skin_temp", skinDelta), xMax })
        : null,
      motion: hasData(s.motion)
        ? motionOption({ theme, ts, values: s.motion!, longRange, height: 110, anomalies: dots("motion", s.motion), xMax })
        : null,
      bed: hasData(s.in_bed) ? inBedOption({ theme, ts, values: s.in_bed!, longRange, height: 70, xMax }) : null,
      snowflake: { buckets: a.length, flagged, stepMin: Math.round(step / 60_000), bucketMin: BUCKET_MS / 60_000 },
      forecast: fc ? { fc, hr: !!fHr, rr: !!fRr, spo2: !!fSpo2 } : null,
    };
  }, [data, anomalies, forecasts, baselines, copd, range, liveHr, fetchedAt, replayEnd, cursor, theme]);

  const group = `resident-${residentId}`;
  const usual = <LegendSwatch color="var(--band)" label={`${pronoun} usual range (p05–p95)`} band />;
  const sf = charts?.snowflake.buckets ? (
    <>
      <LegendSwatch color={pal.expected} label="Snowflake expected (99 %)" band />
      <AnomalySwatch />
    </>
  ) : null;
  const fcLegend = <ForecastSwatch pal={pal} />;
  const fc = charts?.forecast ?? null;

  return (
    <section aria-labelledby="vitals-heading" className="space-y-4 vg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="vitals-heading" className="flex items-center gap-2 text-xl font-bold">
          Vitals {loading && <Loader2 className="size-5 animate-spin text-muted-foreground" aria-label="Loading" />}
        </h2>
        <div role="group" aria-label="Time range" className="flex rounded-xl bg-surface-2 p-1">
          {(Object.keys(RANGES) as RangeKey[]).map((k) => (
            <button
              key={k}
              type="button"
              aria-pressed={range === k}
              onClick={() => setRange(k)}
              className={cn(
                "h-11 min-w-16 rounded-lg px-4 text-base ",
                range === k ? "bg-surface font-bold text-foreground shadow-[var(--shadow)]" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {RANGES[k].label}
            </button>
          ))}
        </div>
      </div>
      {error && (
        <p className="rounded-lg bg-crit-bg p-3 text-base text-crit" role="alert">
          {error}
        </p>
      )}
      {fc && <ForecastCallout fc={fc.fc} />}
      {!charts ? (
        <div className="flex h-64 items-center justify-center text-muted-foreground">
          {loading ? "Loading charts…" : "No readings in this range"}
        </div>
      ) : (
        <div className="space-y-4">
          <ChartPanel
            title="Heart rate"
            hint="bpm"
            group={group}
            height={200}
            option={charts.hr}
            legend={
              <span className="flex flex-wrap gap-3">
                {polar && replayEnd === null && now !== null && now - polar.receivedAt < 5000 && (
                  <span className="flex items-center gap-1.5 text-sm text-accent">
                    <span className="size-2 animate-pulse rounded-full bg-accent" aria-hidden />
                    Polar live{typeof polar.hr === "number" ? ` · ${polar.hr} bpm` : ""}
                  </span>
                )}
                <LegendSwatch color="var(--text)" label="Heart rate" />
                {usual}
                {sf}
                {fc?.hr && fcLegend}
              </span>
            }
          />
          <ChartPanel
            title="Respiratory rate"
            hint="/min · bed sensor, while in bed"
            group={group}
            height={150}
            option={charts.rr}
            legend={
              charts.rr ? (
                <span className="flex flex-wrap gap-3">
                  {usual}
                  {sf}
                  {fc?.rr && fcLegend}
                </span>
              ) : undefined
            }
            empty="— no readings in this range (measured only while in bed)"
          />
          <ChartPanel
            title="SpO₂"
            hint={copd ? "% · NEWS2 scale 2 (COPD)" : "% · shaded = NEWS2 points"}
            group={group}
            height={150}
            option={charts.spo2}
            legend={
              <span className="flex flex-wrap gap-3">
                <LegendSwatch color={pal.zone[1]} label="NEWS2 +1" band />
                <LegendSwatch color={pal.zone[2]} label="+2" band />
                <LegendSwatch color={pal.zone[3]} label="+3" band />
                {fc?.spo2 && fcLegend}
              </span>
            }
          />
          <ChartPanel
            title="Skin temperature"
            hint="°C change vs usual (wrist, not core)"
            group={group}
            height={130}
            option={charts.skin}
          />
          <ChartPanel
            title="Movement"
            hint="wrist motion counts"
            group={group}
            height={110}
            option={charts.motion}
            legend={charts.snowflake.flagged ? <AnomalySwatch /> : undefined}
          />
          <ChartPanel title="In bed" hint="bed sensor" group={group} height={70} option={charts.bed} />
          <p className="text-sm text-muted-foreground">
            Source: Tiger {data?.res === "1h" ? "vitals_1h" : data?.res === "15m" ? "vitals_15m" : "vitals_1m"} continuous
            aggregate · gap-filled with locf.{" "}
            {charts.snowflake.buckets > 0
              ? `Expected range and dots from Snowflake ANOMALY_DETECTION on closed 15-min buckets (${charts.snowflake.flagged} of ${charts.snowflake.buckets} flagged in this range).`
              : "Snowflake anomaly results: none for this range yet."}
          </p>
        </div>
      )}
    </section>
  );
}

function ForecastSwatch({ pal }: { pal: (typeof PALETTES)["night"] }) {
  return (
    <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
      <svg width="22" height="10" aria-hidden className="shrink-0">
        <rect x="0" y="1" width="22" height="8" rx="2" fill={pal.forecastBand} />
        <line x1="1" y1="5" x2="21" y2="5" stroke={pal.forecast} strokeWidth="2" strokeDasharray="4 3" />
      </svg>
      Snowflake forecast (90 %)
    </span>
  );
}

/** "RR forecast to cross 25 by ~03:00" from the newest Snowflake FORECAST, with when it was made. */
function ForecastCallout({ fc }: { fc: Forecast }) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl px-3 py-2 text-base ring-1",
        fc.callout ? "bg-high-bg text-high ring-high-solid" : "bg-surface-2 text-muted-foreground ring-line",
      )}
      role="status"
    >
      <TrendingUp className="size-5 shrink-0 text-high" aria-hidden />
      <span className="font-bold">{fc.callout ?? "Forecast: no NEWS2 3-point threshold crossed within 4 h"}</span>
      <span className="text-sm text-muted-foreground">
        Snowflake ML.FORECAST · made {facilityHHMM(Date.parse(fc.as_of))} from the last {fc.train_hours} h · dashed line = next 2 h
      </span>
    </div>
  );
}

function AnomalySwatch() {
  return (
    <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
      <span aria-hidden className="size-2.5 rounded-full bg-high-solid" />
      Unusual for this resident (Snowflake)
    </span>
  );
}
