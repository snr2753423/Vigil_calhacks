// ECharts options for the resident's synced vitals charts (SPEC §11.2). One measure per chart (no dual
// axes), 2 px lines, recessive grid, crosshair tooltip; the personal baseline is a muted band.
import type { EChartsOption } from "echarts";

import { facilityDayHHMM, facilityHHMM } from "@/lib/format";
import { spo2Zones, type Band } from "@/lib/vitals";

export const INK = { muted: "#94a3b8", faint: "#64748b", grid: "rgba(148,163,184,0.12)", axis: "rgba(148,163,184,0.3)" };
export const LINE = "#5eead4"; // teal: the measured value
export const BAND = "rgba(148,163,184,0.22)"; // the resident's usual range
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

function tooltipFormatter(unit: string, digits: number, band?: Band, ts?: number[]) {
  return (params: unknown) => {
    const list = (Array.isArray(params) ? params : [params]) as { seriesName: string; value: Pair; dataIndex: number }[];
    const main = list.find((p) => !p.seriesName.startsWith("_") && p.seriesName !== "Usual range");
    if (!main) return "";
    const [t, v] = main.value;
    let html = `<div style="font-weight:600">${facilityDayHHMM(t)}</div>${main.seriesName}: <b>${
      v === null ? "—" : `${v.toFixed(digits)}${unit}`
    }</b>`;
    if (band && ts) {
      const i = main.dataIndex;
      const lo = band.lower[i];
      const w = band.width[i];
      if (lo !== null && w !== null) html += `<br/>Usual: ${lo.toFixed(digits)}–${(lo + w).toFixed(digits)}${unit}`;
    }
    return html;
  };
}

export function bandedLineOption(opts: {
  ts: number[];
  values: Series;
  band: Band;
  name: string;
  unit: string;
  digits: number;
  longRange: boolean;
  height: number;
}): EChartsOption {
  const { ts, values, band, name, unit, digits, longRange, height } = opts;
  const base = frame(ts, longRange, height);
  return {
    ...base,
    tooltip: { ...(base.tooltip as object), formatter: tooltipFormatter(unit, digits, band, ts) },
    yAxis: yAxis(),
    series: [...bandSeries(ts, band, "band"), line(name, pairs(ts, values))],
  };
}

export function spo2Option(opts: { ts: number[]; values: Series; scale: 1 | 2; longRange: boolean; height: number }): EChartsOption {
  const { ts, values, scale, longRange, height } = opts;
  const base = frame(ts, longRange, height);
  const nums = values.filter((v): v is number => v !== null);
  const floor = Math.min(scale === 2 ? 82 : 88, ...nums.map((v) => Math.floor(v) - 1));
  return {
    ...base,
    tooltip: { ...(base.tooltip as object), formatter: tooltipFormatter("%", 0) },
    yAxis: yAxis({ min: floor, max: 100, scale: false }),
    series: [
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

export function deltaOption(opts: { ts: number[]; values: Series; longRange: boolean; height: number }): EChartsOption {
  const { ts, values, longRange, height } = opts;
  const base = frame(ts, longRange, height);
  return {
    ...base,
    tooltip: { ...(base.tooltip as object), formatter: tooltipFormatter(" °C vs usual", 2) },
    yAxis: yAxis({ axisLabel: { color: INK.muted, fontSize: 12, formatter: (v: number) => `${v > 0 ? "+" : ""}${v}` } }),
    series: [
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

export function motionOption(opts: { ts: number[]; values: Series; longRange: boolean; height: number }): EChartsOption {
  const { ts, values, longRange, height } = opts;
  const base = frame(ts, longRange, height);
  return {
    ...base,
    tooltip: { ...(base.tooltip as object), formatter: tooltipFormatter(" counts", 0) },
    yAxis: yAxis({ min: 0, scale: false }),
    series: [
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
