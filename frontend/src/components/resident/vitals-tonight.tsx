"use client";

import { useVitalsWindow } from "@/hooks/use-vitals";
import type { AnomalyBucket, AnomalyMetricKey, VitalBaselines } from "@/lib/types";
import { anomalyPoints, baselineBand, todBlock } from "@/lib/vitals";

const SPAN_MS = 8 * 3600_000;
const STEP_MS = 15 * 60_000; // replay: reload as the cursor crosses each 15-min bucket
const REFRESH_MS = 60_000;
const W = 600;
const H = 84;

type Metric = { key: AnomalyMetricKey; label: string; unit: string; digits: number; perMinute?: boolean };

const METRICS: Metric[] = [
  { key: "rr", label: "Breathing", unit: "/min", digits: 0 },
  { key: "hr", label: "Heart rate", unit: "", digits: 0 },
  { key: "spo2", label: "Oxygen", unit: "%", digits: 0 },
  { key: "motion", label: "Restlessness", unit: "", digits: 0, perMinute: true },
];

type Props = { residentId: number; baselines: VitalBaselines; her: string };

/** The last 8 hours of breathing, heart rate, oxygen and restlessness from Tiger's 15-min aggregate, each over the
 *  resident's usual range (shaded) with Snowflake-flagged buckets as dots. Plain; the full charts are in Details. */
export function VitalsTonight({ residentId, baselines, her }: Props) {
  const { data, anomalies: flags } = useVitalsWindow({
    residentId,
    metrics: "hr,rr,spo2,motion",
    spanMs: SPAN_MS,
    res: "15m",
    stepMs: STEP_MS,
    refreshMs: REFRESH_MS,
  });

  const ts = data?.ts.map((t) => Date.parse(t)) ?? [];
  return (
    <section className="vg-card px-6 py-[22px]" aria-labelledby="tonight-heading">
      <h2 id="tonight-heading" className="vg-label mb-3">
        Vitals tonight <span className="normal-case">· the shaded band is {her} usual range</span>
      </h2>
      <div className="grid gap-3.5">
        {METRICS.map((m) => (
          <VitalRow key={m.key} m={m} ts={ts} values={data?.series[m.key] ?? []} baselines={baselines} flags={flags} her={her} />
        ))}
      </div>
      <p className="mt-2.5 text-sm text-soft">
        Dots mark readings Snowflake&apos;s model flagged as unusual for {her === "his" ? "him" : "her"}. Exact charts are
        in <a href="#details" className="underline underline-offset-2">Details</a> below.
      </p>
    </section>
  );
}

function VitalRow({ m, ts, values: raw, baselines, flags, her }: {
  m: Metric;
  ts: number[];
  values: (number | null)[];
  baselines: VitalBaselines;
  flags: AnomalyBucket[];
  her: string;
}) {
  // Movement is summed per bucket; the baseline is per minute.
  const values = m.perMinute ? raw.map((v) => (v === null ? null : v / 15)) : raw;
  const band = baselineBand(ts, baselines, m.key);
  const last = lastIndex(values);
  const nums = values.filter((v): v is number => v !== null);

  if (last < 0 || nums.length < 2) {
    return (
      <div>
        <span className="text-muted-foreground">{m.label}</span>
        <p className="text-sm text-soft">— not equipped or no readings tonight</p>
      </div>
    );
  }

  const lows = [...nums, ...band.lower.filter((v): v is number => v !== null)];
  const highs = [...nums, ...band.lower.map((l, i) => (l === null || band.width[i] === null ? null : l + (band.width[i] as number))).filter((v): v is number => v !== null)];
  const min = Math.min(...lows);
  const max = Math.max(...highs);
  const pad = (max - min) * 0.12 || 1;
  const y = (v: number) => H - 4 - ((v - (min - pad)) / (max - min + 2 * pad)) * (H - 8);
  const x = (i: number) => (ts.length < 2 ? 0 : (i / (ts.length - 1)) * (W - 8) + 4);

  let line = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v === null) {
      pen = false;
    } else {
      line += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`;
      pen = true;
    }
  });
  let bandPath = "";
  const top: string[] = [];
  const bottom: string[] = [];
  band.lower.forEach((l, i) => {
    const w = band.width[i];
    if (l === null || w === null) return;
    top.push(`${x(i).toFixed(1)} ${y(l + w).toFixed(1)}`);
    bottom.unshift(`${x(i).toFixed(1)} ${y(l).toFixed(1)}`);
  });
  if (top.length > 1) bandPath = `M${top.join("L")}L${bottom.join("L")}Z`;
  const tIndex = new Map(ts.map((t, i) => [t, i]));
  const dots = anomalyPoints(ts, values, flags, m.key);

  const now = values[last] as number;
  const blockStats = baselines[m.key]?.[String(todBlock(ts[last]))];
  let shown: string;
  let usual: string;
  if (m.key === "motion") {
    const ratio = blockStats ? now / Math.max(blockStats.p50, 0.5) : null;
    shown = ratio === null ? "—" : ratio >= 1.5 ? `${ratio.toFixed(0)}×` : "usual";
    usual = ratio !== null && ratio >= 1.5 ? `${her} usual` : "about as usual";
  } else {
    shown = `${now.toFixed(m.digits)}${m.unit === "%" ? "%" : ""}`;
    usual = blockStats ? `usual ${blockStats.p05.toFixed(0)}–${blockStats.p95.toFixed(0)}` : "";
  }

  return (
    <div className="grid grid-cols-[1fr_84px] items-center gap-x-3.5 gap-y-1 [grid-template-areas:'lbl_lbl'_'chart_now']">
      <span className="text-muted-foreground [grid-area:lbl]">{m.label}</span>
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full [grid-area:chart]" role="img"
        aria-label={`${m.label} over the last 8 hours, now ${shown}${usual ? `, ${usual}` : ""}`}>
        {bandPath && <path d={bandPath} fill="var(--band)" />}
        <path d={line} fill="none" stroke="var(--text)" strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        {dots.map((d) => {
          const i = tIndex.get(d.t);
          return i === undefined ? null : <circle key={d.bucket} cx={x(i)} cy={y(d.value)} r={5} fill="var(--high)" />;
        })}
      </svg>
      <span className="text-right text-lg font-bold [grid-area:now]">
        {shown}
        {usual && <small className="block text-[13px] font-normal text-soft">{usual}</small>}
      </span>
    </div>
  );
}

function lastIndex(values: (number | null)[]): number {
  for (let i = values.length - 1; i >= 0; i--) if (values[i] !== null) return i;
  return -1;
}
