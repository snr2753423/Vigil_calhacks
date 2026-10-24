"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { cn } from "cn";

import { ChartPanel, LegendSwatch } from "@/components/resident/chart-panel";
import { getJSON } from "@/lib/api";
import {
  ANOMALY,
  BAND,
  bandedLineOption,
  EXPECTED,
  deltaOption,
  inBedOption,
  LINE,
  motionOption,
  spo2Option,
  ZONE,
} from "@/lib/chart-options";
import type { AnomalyBucket, AnomalyResponse, VitalBaselines, VitalsResponse } from "@/lib/types";
import {
  anomalyPoints,
  BUCKET_MS,
  baselineBand,
  deltaVsBaseline,
  expectedBand,
  hasData,
  RANGES,
  type RangeKey,
} from "@/lib/vitals";

const METRICS = "hr,rr,spo2,skin_temp,motion,in_bed";

type Props = { residentId: number; baselines: VitalBaselines; copd: boolean; pronoun: string };

/** HR / RR / SpO₂ / skin-temp change / movement / bed presence from Tiger's continuous aggregates
 *  (vitals_1m for 6 h, vitals_15m for 24 h, vitals_1h for 7 d), with a shared crosshair. */
export function VitalsCharts({ residentId, baselines, copd, pronoun }: Props) {
  const [range, setRange] = useState<RangeKey>("6h");
  const [data, setData] = useState<VitalsResponse | null>(null);
  const [anomalies, setAnomalies] = useState<AnomalyBucket[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const r = RANGES[range];
    const ctrl = new AbortController();
    const load = async () => {
      setLoading(true);
      // No `to`: the backend uses its own now, so a fast device clock can't send a "future" timestamp.
      const from = new Date(Date.now() - r.ms);
      const q = new URLSearchParams({ metrics: METRICS, from: from.toISOString(), res: r.res });
      // Snowflake overlay (cached in Tiger): optional — the charts never wait on it or fail because of it.
      const aq = new URLSearchParams({ kind: "anomaly", from: from.toISOString() });
      const overlay = getJSON<AnomalyResponse>(`/api/insights/${residentId}?${aq}`, ctrl.signal)
        .then((r) => r.items)
        .catch(() => null);
      try {
        setData(await getJSON<VitalsResponse>(`/api/residents/${residentId}/vitals?${q}`, ctrl.signal));
        setError(null);
      } catch (e) {
        if (!ctrl.signal.aborted) setError(e instanceof Error ? e.message : "Could not load vitals");
      } finally {
        if (!ctrl.signal.aborted) setLoading(false);
      }
      const items = await overlay;
      if (!ctrl.signal.aborted && items) setAnomalies(items);
    };
    void load();
    const t = setInterval(load, r.refreshMs);
    return () => {
      clearInterval(t);
      ctrl.abort();
    };
  }, [residentId, range]);

  const charts = useMemo(() => {
    if (!data || data.ts.length < 2) return null;
    const ts = data.ts.map((t) => Date.parse(t));
    const s = data.series;
    const longRange = range !== "6h";
    const hrBand = baselineBand(ts, baselines, "hr");
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
    return {
      hr: hasData(s.hr)
        ? bandedLineOption({ ts, values: s.hr!, band: hrBand, name: "Heart rate", unit: " bpm", digits: 0, longRange, height: 200, expected: exp("hr"), anomalies: dots("hr", s.hr) })
        : null,
      rr: hasData(s.rr)
        ? bandedLineOption({ ts, values: s.rr!, band: rrBand, name: "Resp. rate", unit: "/min", digits: 1, longRange, height: 150, expected: exp("rr"), anomalies: dots("rr", s.rr) })
        : null,
      spo2: hasData(s.spo2)
        ? spo2Option({ ts, values: s.spo2!, scale: copd ? 2 : 1, longRange, height: 150, expected: exp("spo2"), anomalies: dots("spo2", s.spo2) })
        : null,
      skin: hasData(skinDelta)
        ? deltaOption({ ts, values: skinDelta, longRange, height: 130, expected: skinExp, anomalies: dots("skin_temp", skinDelta) })
        : null,
      motion: hasData(s.motion)
        ? motionOption({ ts, values: s.motion!, longRange, height: 110, anomalies: dots("motion", s.motion) })
        : null,
      bed: hasData(s.in_bed) ? inBedOption({ ts, values: s.in_bed!, longRange, height: 70 }) : null,
      snowflake: { buckets: a.length, flagged, stepMin: Math.round(step / 60_000), bucketMin: BUCKET_MS / 60_000 },
    };
  }, [data, anomalies, baselines, copd, range]);

  const group = `resident-${residentId}`;
  const usual = <LegendSwatch color={BAND} label={`${pronoun} usual range (p05–p95)`} band />;
  const sf = charts?.snowflake.buckets ? (
    <>
      <LegendSwatch color={EXPECTED} label="Snowflake expected (99 %)" band />
      <AnomalySwatch />
    </>
  ) : null;

  return (
    <section aria-labelledby="vitals-heading" className="space-y-4 rounded-2xl bg-card p-4 ring-1 ring-white/10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="vitals-heading" className="flex items-center gap-2 text-xl font-semibold">
          Vitals {loading && <Loader2 className="size-5 animate-spin text-muted-foreground" aria-label="Loading" />}
        </h2>
        <div role="group" aria-label="Time range" className="flex rounded-xl bg-white/5 p-1">
          {(Object.keys(RANGES) as RangeKey[]).map((k) => (
            <button
              key={k}
              type="button"
              aria-pressed={range === k}
              onClick={() => setRange(k)}
              className={cn(
                "h-11 min-w-16 rounded-lg px-4 text-base font-medium",
                range === k ? "bg-white/15 text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {RANGES[k].label}
            </button>
          ))}
        </div>
      </div>
      {error && (
        <p className="rounded-lg bg-red-500/10 p-3 text-base text-red-100" role="alert">
          {error}
        </p>
      )}
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
                <LegendSwatch color={LINE} label="Heart rate" />
                {usual}
                {sf}
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
                <LegendSwatch color={ZONE[1]} label="+1" band />
                <LegendSwatch color={ZONE[2]} label="+2" band />
                <LegendSwatch color={ZONE[3]} label="+3" band />
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
              ? `Expected range and ◆ from Snowflake ANOMALY_DETECTION on closed 15-min buckets (${charts.snowflake.flagged} of ${charts.snowflake.buckets} flagged in this range).`
              : "Snowflake anomaly results: none for this range yet."}
          </p>
        </div>
      )}
    </section>
  );
}

function AnomalySwatch() {
  return (
    <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
      <span aria-hidden style={{ color: ANOMALY }}>
        ◆
      </span>
      Outside expected (Snowflake)
    </span>
  );
}
