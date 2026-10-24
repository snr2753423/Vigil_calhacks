// ECharts options for the resident's synced vitals charts (SPEC §11.2). One measure per chart (no dual
// axes), 2 px lines, recessive grid, crosshair tooltip; the personal baseline is a muted band.
import type { EChartsOption } from "echarts";

import { facilityDayHHMM, facilityHHMM } from "@/lib/format";
import { spo2Zones, type AnomalyPoint, type Band } from "@/lib/vitals";

export const INK = { muted: "#94a3b8", faint: "#64748b", grid: "rgba(148,163,184,0.12)", axis: "rgba(148,163,184,0.3)" };
export const LINE = "#5eead4"; // teal: the measured value
export const BAND = "rgba(148,163,184,0.22)"; // the resident's usual range
export const EXPECTED = "rgba(129,140,248,0.16)"; // Snowflake model's expected range (99 % interval)
export const EXPECTED_EDGE = "rgba(165,180,252,0.75)";
export const ANOMALY = "#fbbf24"; // amber diamonds: buckets Snowflake flagged
export const ZONE = { 1: "rgba(252,211,77,0.16)", 2: "rgba(251,146,60,0.2)", 3: "rgba(239,68,68,0.22)" } as const;

type Series = (number | null)[];
type Pair = [number, number | null];

const pairs = (ts: number[], v: Series): Pair[] => ts.map((t, i) => [t, v[i] ?? null]);

function frame(ts: number[], longRange: boolean, height: number): EChartsOption {
  const fmt = (v: number) => (longRange ? facilityDayHHMM(v) : facilityHHMM(v));
  return {
    animation: false,
    grid: { left: 52, right: 16, top: 10, bottom: height > 100 ? 26 : 6 },
    xAxis: {
      type: "time",
      min: ts[0],
      max: ts[ts.length - 1],
      axisLabel: { show: height > 100, color: INK.muted, fontSize: 12, formatter: fmt, hideOverlap: true },
      axisLine: { lineStyle: { color: INK.axis } },
      axisTick: { show: false },
      splitLine: { show: false },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: INK.muted } },
      backgroundColor: "#1e293b",
      borderColor: "rgba(255,255,255,0.12)",
      textStyle: { color: "#e2e8f0", fontSize: 13 },
    },
  };
}

function yAxis(extra: Record<string, unknown> = {}): EChartsOption["yAxis"] {
  return {
    type: "value",
    scale: true,
    axisLabel: { color: INK.muted, fontSize: 12 },
    splitLine: { lineStyle: { color: INK.grid } },
    splitNumber: 3,
    ...extra,
  };
}

const line = (name: string, data: Pair[], color = LINE) => ({
  name,
  type: "line" as const,
  data,
  showSymbol: false,
  connectNulls: false,
  lineStyle: { width: 2, color },
  itemStyle: { color },
  z: 3,
});

/** The p05–p95 band as a stacked pair: an invisible lower edge plus the filled width. */
const bandSeries = (ts: number[], band: Band, stack: string) => [
  {
    name: "_lower",
    type: "line" as const,
    data: pairs(ts, band.lower),
    stack,
    lineStyle: { opacity: 0 },
    showSymbol: false,
    silent: true,
    tooltip: { show: false },
  },
  {
    name: "Usual range",
    type: "line" as const,
    data: pairs(ts, band.width),
    stack,
    lineStyle: { opacity: 0 },
    areaStyle: { color: BAND },
    showSymbol: false,
    silent: true,
    tooltip: { show: false },
  },
];

const EXPECTED_NAME = "Expected range";
const ANOMALY_NAME = "Anomaly";
const OVERLAYS = new Set(["Usual range", EXPECTED_NAME, ANOMALY_NAME]);

/** The Snowflake expected range: dashed edges around a light fill, stacked like the usual band. */
const expectedSeries = (ts: number[], band: Band) => [
  {
    name: "_exp_lower",
    type: "line" as const,
    data: pairs(ts, band.lower),
    stack: "expected",
    lineStyle: { width: 1, type: "dashed" as const, color: EXPECTED_EDGE },
    showSymbol: false,
    silent: true,
    tooltip: { show: false },
    z: 2,
  },
  {
    name: EXPECTED_NAME,
    type: "line" as const,
    data: pairs(ts, band.width),
    stack: "expected",
    lineStyle: { width: 1, type: "dashed" as const, color: EXPECTED_EDGE },
    areaStyle: { color: EXPECTED },
    showSymbol: false,
    silent: true,
    tooltip: { show: false },
    z: 2,
  },
];

const anomalySeries = (points: AnomalyPoint[]) => ({
  name: ANOMALY_NAME,
  type: "scatter" as const,
  data: points.map((p) => [p.t, p.value]),
  symbol: "diamond",
  symbolSize: (_: unknown, p: { dataIndex: number }) => 9 + 6 * (points[p.dataIndex]?.severity ?? 0),
  itemStyle: { color: ANOMALY, borderColor: "#0f172a", borderWidth: 1 },
  tooltip: { show: false },
  z: 5,
});

type Overlays = { band?: Band; expected?: Band; anomalies?: AnomalyPoint[] };

function tooltipFormatter(unit: string, digits: number, { band, expected, anomalies }: Overlays = {}) {
  const flagged = new Set((anomalies ?? []).map((p) => p.t));
  const range = (b: Band | undefined, i: number) => {
    const lo = b?.lower[i];
    const w = b?.width[i];
    return lo == null || w == null ? null : `${lo.toFixed(digits)}–${(lo + w).toFixed(digits)}${unit}`;
  };
  return (params: unknown) => {
    const list = (Array.isArray(params) ? params : [params]) as { seriesName: string; value: Pair; dataIndex: number }[];
    const main = list.find((p) => !p.seriesName.startsWith("_") && !OVERLAYS.has(p.seriesName));
    if (!main) return "";
    const [t, v] = main.value;
    const i = main.dataIndex;
    let html = `<div style="font-weight:600">${facilityDayHHMM(t)}</div>${main.seriesName}: <b>${
      v === null ? "—" : `${v.toFixed(digits)}${unit}`
    }</b>`;
    const usual = range(band, i);
    if (usual) html += `<br/>Usual: ${usual}`;
    const exp = range(expected, i);
    if (exp) html += `<br/>Snowflake expected: ${exp}`;
    if (flagged.has(t)) html += `<br/><span style="color:${ANOMALY}">◆ Outside the expected range (Snowflake)</span>`;
    return html;
  };
}

/** Series for the optional Snowflake overlays. */
const overlaySeries = (ts: number[], { expected, anomalies }: Overlays) => [
  ...(expected ? expectedSeries(ts, expected) : []),
  ...(anomalies?.length ? [anomalySeries(anomalies)] : []),
];

export function bandedLineOption(opts: {
  ts: number[];
  values: Series;
  band: Band;
  name: string;
  unit: string;
  digits: number;
  longRange: boolean;
  height: number;
} & Pick<Overlays, "expected" | "anomalies">): EChartsOption {
  const { ts, values, band, name, unit, digits, longRange, height, expected, anomalies } = opts;
  const base = frame(ts, longRange, height);
  return {
    ...base,
    tooltip: { ...(base.tooltip as object), formatter: tooltipFormatter(unit, digits, { band, expected, anomalies }) },
    yAxis: yAxis(),
    series: [...bandSeries(ts, band, "band"), ...overlaySeries(ts, { expected, anomalies }), line(name, pairs(ts, values))],
  };
}

export function spo2Option(
  opts: { ts: number[]; values: Series; scale: 1 | 2; longRange: boolean; height: number } & Overlays,
): EChartsOption {
  const { ts, values, scale, longRange, height, expected, anomalies } = opts;
  const base = frame(ts, longRange, height);
  const nums = values.filter((v): v is number => v !== null);
  const floor = Math.min(scale === 2 ? 82 : 88, ...nums.map((v) => Math.floor(v) - 1));
  return {
    ...base,
    tooltip: { ...(base.tooltip as object), formatter: tooltipFormatter("%", 0, { expected, anomalies }) },
    yAxis: yAxis({ min: floor, max: 100, scale: false }),
    series: [
      ...overlaySeries(ts, { expected, anomalies }),
      {
        // SpO₂ is read every 5 min, so most 1-min buckets are empty: join the readings and mark each one.
        ...line("SpO₂", pairs(ts, values)),
        connectNulls: true,
        showSymbol: !longRange,
        symbolSize: 4,
        markArea: {
          silent: true,
          label: { show: false }, // named in the chart's HTML legend instead of over the line
          data: spo2Zones(scale).map((z) => [
            { yAxis: Math.max(z.from, floor), itemStyle: { color: ZONE[z.points] } },
            { yAxis: z.to },
          ]),
        },
      },
    ],
  };
}

export function deltaOption(
  opts: { ts: number[]; values: Series; longRange: boolean; height: number } & Overlays,
): EChartsOption {
  const { ts, values, longRange, height, expected, anomalies } = opts;
  const base = frame(ts, longRange, height);
  return {
    ...base,
    tooltip: { ...(base.tooltip as object), formatter: tooltipFormatter(" °C vs usual", 2, { expected, anomalies }) },
    yAxis: yAxis({ axisLabel: { color: INK.muted, fontSize: 12, formatter: (v: number) => `${v > 0 ? "+" : ""}${v}` } }),
    series: [
      ...overlaySeries(ts, { expected, anomalies }),
      {
        ...line("Skin temp change", pairs(ts, values)),
        markLine: {
          silent: true,
          symbol: "none",
          label: { show: false },
          lineStyle: { color: INK.faint, type: "dashed" },
          data: [{ yAxis: 0 }],
        },
      },
    ],
  };
}

export function motionOption(
  opts: { ts: number[]; values: Series; longRange: boolean; height: number } & Pick<Overlays, "anomalies">,
): EChartsOption {
  const { ts, values, longRange, height, anomalies } = opts;
  const base = frame(ts, longRange, height);
  return {
    ...base,
    tooltip: { ...(base.tooltip as object), formatter: tooltipFormatter(" counts", 0, { anomalies }) },
    yAxis: yAxis({ min: 0, scale: false }),
    series: [
      ...overlaySeries(ts, { anomalies }),
      {
        name: "Movement",
        type: "bar",
        data: pairs(ts, values),
        barMaxWidth: 6,
        itemStyle: { color: "#818cf8", borderRadius: [2, 2, 0, 0] },
      },
    ],
  };
}

export function inBedOption(opts: { ts: number[]; values: Series; longRange: boolean; height: number }): EChartsOption {
  const { ts, values, longRange, height } = opts;
  const base = frame(ts, longRange, height);
  return {
    ...base,
    grid: { left: 52, right: 16, top: 4, bottom: 26 },
    xAxis: { ...(base.xAxis as object), axisLabel: { show: true, color: INK.muted, fontSize: 12, hideOverlap: true, formatter: (v: number) => (longRange ? facilityDayHHMM(v) : facilityHHMM(v)) } },
    tooltip: {
      ...(base.tooltip as object),
      formatter: (params: unknown) => {
        const p = (Array.isArray(params) ? params[0] : params) as { value: Pair };
        const [t, v] = p.value;
        const txt = v === null ? "no data" : v >= 0.95 ? "in bed" : v <= 0.05 ? "out of bed" : `in bed ${Math.round(v * 100)}% of the time`;
        return `<div style="font-weight:600">${facilityDayHHMM(t)}</div>${txt}`;
      },
    },
    yAxis: { type: "value", min: 0, max: 1, show: false },
    series: [
      {
        name: "In bed",
        type: "line",
        step: "middle",
        data: pairs(ts, values),
        showSymbol: false,
        lineStyle: { width: 0 },
        areaStyle: { color: "rgba(129,140,248,0.55)" },
      },
    ],
  };
}
