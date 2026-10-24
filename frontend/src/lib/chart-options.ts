// ECharts options for the resident's synced vitals charts (SPEC §11.2). One measure per chart (no dual
// axes), 2 px lines, recessive grid, crosshair tooltip; the personal baseline is a muted band. Colours come
// from the day or night palette (Phase 11 tokens).
import type { EChartsOption } from "echarts";

import { facilityDayHHMM, facilityHHMM } from "@/lib/format";
import { spo2Zones, type AnomalyPoint, type Band } from "@/lib/vitals";

export type ChartTheme = "day" | "night";

/** Canvas colours per theme (ECharts can't read CSS variables): the mockup's tokens. */
export type ChartPalette = {
  muted: string;
  faint: string;
  grid: string;
  axis: string;
  line: string; // the measured value
  band: string; // the resident's usual range
  expected: string; // Snowflake model's expected range (99 % interval)
  expectedEdge: string;
  anomaly: string; // dots: buckets Snowflake flagged
  anomalyRing: string;
  forecast: string; // dashed: the Snowflake FORECAST
  forecastBand: string;
  threshold: string; // the NEWS2 3-point threshold a forecast crosses
  zone: Record<1 | 2 | 3, string>;
  motion: string;
  bed: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
  series: string[]; // Ask charts: categorical
};

export const PALETTES: Record<ChartTheme, ChartPalette> = {
  night: {
    muted: "#b9ab98",
    faint: "#8e8273",
    grid: "rgba(241,230,214,0.07)",
    axis: "#3b352e",
    line: "#f1e6d6",
    band: "rgba(217,163,91,0.13)",
    expected: "rgba(154,165,159,0.14)",
    expectedEdge: "rgba(185,171,152,0.55)",
    anomaly: "#e3a556",
    anomalyRing: "#1c1916",
    forecast: "#e3a556",
    forecastBand: "rgba(227,165,86,0.12)",
    threshold: "#e8836c",
    zone: { 1: "rgba(205,187,106,0.1)", 2: "rgba(227,165,86,0.13)", 3: "rgba(232,131,108,0.15)" },
    motion: "#8e8273",
    bed: "rgba(217,163,91,0.4)",
    tooltipBg: "#2f2a25",
    tooltipBorder: "#3b352e",
    tooltipText: "#f1e6d6",
    series: ["#d9a35b", "#9aa59f", "#cdbb6a", "#e8836c", "#b9ab98", "#7fa69a"],
  },
  day: {
    muted: "#6f665c",
    faint: "#8f857a",
    grid: "rgba(43,39,35,0.08)",
    axis: "#e2d8c9",
    line: "#2b2723",
    band: "rgba(63,111,99,0.14)",
    expected: "rgba(93,107,102,0.12)",
    expectedEdge: "rgba(93,107,102,0.55)",
    anomaly: "#b36b12",
    anomalyRing: "#fffdf9",
    forecast: "#b36b12",
    forecastBand: "rgba(179,107,18,0.1)",
    threshold: "#b4432f",
    zone: { 1: "rgba(122,106,28,0.1)", 2: "rgba(179,107,18,0.12)", 3: "rgba(180,67,47,0.13)" },
    motion: "#8f857a",
    bed: "rgba(63,111,99,0.4)",
    tooltipBg: "#fffdf9",
    tooltipBorder: "#e2d8c9",
    tooltipText: "#2b2723",
    series: ["#3f6f63", "#b36b12", "#7a6a1c", "#b4432f", "#6f665c", "#5d6b66"],
  },
};

type Series = (number | null)[];
type Pair = [number, number | null];

const pairs = (ts: number[], v: Series): Pair[] => ts.map((t, i) => [t, v[i] ?? null]);

function frame(ts: number[], longRange: boolean, height: number, pal: ChartPalette, xMax?: number): EChartsOption {
  const fmt = (v: number) => (longRange ? facilityDayHHMM(v) : facilityHHMM(v));
  return {
    animation: false,
    grid: { left: 52, right: 16, top: 10, bottom: height > 100 ? 26 : 6 },
    xAxis: {
      type: "time",
      min: ts[0],
      max: Math.max(ts[ts.length - 1], xMax ?? 0),
      axisLabel: { show: height > 100, color: pal.muted, fontSize: 12, formatter: fmt, hideOverlap: true },
      axisLine: { lineStyle: { color: pal.axis } },
      axisTick: { show: false },
      splitLine: { show: false },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: pal.muted } },
      backgroundColor: pal.tooltipBg,
      borderColor: pal.tooltipBorder,
      textStyle: { color: pal.tooltipText, fontSize: 14 },
    },
  };
}

function yAxis(pal: ChartPalette, extra: Record<string, unknown> = {}): EChartsOption["yAxis"] {
  return {
    type: "value",
    scale: true,
    axisLabel: { color: pal.muted, fontSize: 12 },
    splitLine: { lineStyle: { color: pal.grid } },
    splitNumber: 3,
    ...extra,
  };
}

const line = (name: string, data: Pair[], color: string) => ({
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
const bandSeries = (ts: number[], band: Band, stack: string, pal: ChartPalette) => [
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
    areaStyle: { color: pal.band },
    showSymbol: false,
    silent: true,
    tooltip: { show: false },
  },
];

const EXPECTED_NAME = "Expected range";
const ANOMALY_NAME = "Anomaly";
const FORECAST_NAME = "Snowflake forecast";
const FORECAST_RANGE_NAME = "Forecast range";
const OVERLAYS = new Set(["Usual range", EXPECTED_NAME, ANOMALY_NAME, FORECAST_NAME, FORECAST_RANGE_NAME]);

/** A Snowflake FORECAST drawn past the last reading: points, their 90 % interval, and the NEWS2 3-point threshold
 *  it crosses (if any). */
export type ForecastOverlay = {
  ts: number[];
  value: (number | null)[];
  lower: (number | null)[];
  upper: (number | null)[];
  threshold: number | null;
};

const forecastSeries = (f: ForecastOverlay, pal: ChartPalette) => [
  {
    name: "_fc_lower",
    type: "line" as const,
    data: pairs(f.ts, f.lower),
    stack: "forecast",
    lineStyle: { opacity: 0 },
    showSymbol: false,
    silent: true,
    tooltip: { show: false },
    z: 2,
  },
  {
    name: FORECAST_RANGE_NAME,
    type: "line" as const,
    data: pairs(f.ts, f.upper.map((u, i) => (u === null || f.lower[i] === null ? null : u - (f.lower[i] as number)))),
    stack: "forecast",
    lineStyle: { opacity: 0 },
    areaStyle: { color: pal.forecastBand },
    showSymbol: false,
    silent: true,
    tooltip: { show: false },
    z: 2,
  },
  {
    name: FORECAST_NAME,
    type: "line" as const,
    data: pairs(f.ts, f.value),
    showSymbol: false,
    lineStyle: { width: 2, type: "dashed" as const, color: pal.forecast },
    itemStyle: { color: pal.forecast },
    z: 4,
    ...(f.threshold !== null
      ? {
          markLine: {
            silent: true,
            symbol: "none",
            label: { show: true, position: "insideEndTop" as const, color: pal.threshold, fontSize: 12, formatter: "NEWS2 +3" },
            lineStyle: { color: pal.threshold, type: "dashed" as const, width: 1 },
            data: [{ yAxis: f.threshold }],
          },
        }
      : {}),
  },
];

/** The Snowflake expected range: dashed edges around a light fill, stacked like the usual band. */
const expectedSeries = (ts: number[], band: Band, pal: ChartPalette) => [
  {
    name: "_exp_lower",
    type: "line" as const,
    data: pairs(ts, band.lower),
    stack: "expected",
    lineStyle: { width: 1, type: "dashed" as const, color: pal.expectedEdge },
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
    lineStyle: { width: 1, type: "dashed" as const, color: pal.expectedEdge },
    areaStyle: { color: pal.expected },
    showSymbol: false,
    silent: true,
    tooltip: { show: false },
    z: 2,
  },
];

const anomalySeries = (points: AnomalyPoint[], pal: ChartPalette) => ({
  name: ANOMALY_NAME,
  type: "scatter" as const,
  data: points.map((p) => [p.t, p.value]),
  symbol: "circle",
  symbolSize: (_: unknown, p: { dataIndex: number }) => 8 + 5 * (points[p.dataIndex]?.severity ?? 0),
  itemStyle: { color: pal.anomaly, borderColor: pal.anomalyRing, borderWidth: 1 },
  tooltip: { show: false },
  z: 5,
});

type Overlays = { band?: Band; expected?: Band; anomalies?: AnomalyPoint[]; forecast?: ForecastOverlay; xMax?: number };

function tooltipFormatter(unit: string, digits: number, pal: ChartPalette, { band, expected, anomalies, forecast }: Overlays = {}) {
  const flagged = new Set((anomalies ?? []).map((p) => p.t));
  const range = (b: Band | undefined, i: number) => {
    const lo = b?.lower[i];
    const w = b?.width[i];
    return lo == null || w == null ? null : `${lo.toFixed(digits)}–${(lo + w).toFixed(digits)}${unit}`;
  };
  return (params: unknown) => {
    const list = (Array.isArray(params) ? params : [params]) as { seriesName: string; value: Pair; dataIndex: number }[];
    const main = list.find((p) => !p.seriesName.startsWith("_") && !OVERLAYS.has(p.seriesName));
    const fc = list.find((p) => p.seriesName === FORECAST_NAME);
    if ((!main || main.value[1] === null) && fc && forecast) {
      const [t, v] = fc.value;
      const lo = forecast.lower[fc.dataIndex];
      const hi = forecast.upper[fc.dataIndex];
      const range = lo == null || hi == null ? "" : `<br/>90 % range ${lo.toFixed(digits)}–${hi.toFixed(digits)}${unit}`;
      return `<div style="font-weight:600">${facilityDayHHMM(t)}</div>Snowflake forecast: <b>${
        v === null ? "—" : `${v.toFixed(digits)}${unit}`
      }</b>${range}`;
    }
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
    if (flagged.has(t)) html += `<br/><span style="color:${pal.anomaly}">● Unusual for this resident (Snowflake)</span>`;
    return html;
  };
}

/** Series for the optional Snowflake overlays. */
const overlaySeries = (ts: number[], pal: ChartPalette, { expected, anomalies, forecast }: Overlays) => [
  ...(expected ? expectedSeries(ts, expected, pal) : []),
  ...(anomalies?.length ? [anomalySeries(anomalies, pal)] : []),
  ...(forecast ? forecastSeries(forecast, pal) : []),
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
  theme: ChartTheme;
} & Pick<Overlays, "expected" | "anomalies" | "forecast" | "xMax">): EChartsOption {
  const { ts, values, band, name, unit, digits, longRange, height, expected, anomalies, forecast, xMax } = opts;
  const pal = PALETTES[opts.theme];
  const base = frame(ts, longRange, height, pal, xMax);
  return {
    ...base,
    tooltip: { ...(base.tooltip as object), formatter: tooltipFormatter(unit, digits, pal, { band, expected, anomalies, forecast }) },
    yAxis: yAxis(pal),
    series: [
      ...bandSeries(ts, band, "band", pal),
      ...overlaySeries(ts, pal, { expected, anomalies, forecast }),
      line(name, pairs(ts, values), pal.line),
    ],
  };
}

export function spo2Option(
  opts: { ts: number[]; values: Series; scale: 1 | 2; longRange: boolean; height: number; theme: ChartTheme } & Overlays,
): EChartsOption {
  const { ts, values, scale, longRange, height, expected, anomalies, forecast, xMax } = opts;
  const pal = PALETTES[opts.theme];
  const base = frame(ts, longRange, height, pal, xMax);
  const nums = [...values, ...(forecast?.lower ?? [])].filter((v): v is number => v !== null);
  const floor = Math.min(scale === 2 ? 82 : 88, ...nums.map((v) => Math.floor(v) - 1));
  return {
    ...base,
    tooltip: { ...(base.tooltip as object), formatter: tooltipFormatter("%", 0, pal, { expected, anomalies, forecast }) },
    yAxis: yAxis(pal, { min: floor, max: 100, scale: false }),
    series: [
      ...overlaySeries(ts, pal, { expected, anomalies, forecast }),
      {
        // SpO₂ is read every 5 min, so most 1-min buckets are empty: join the readings and mark each one.
        ...line("SpO₂", pairs(ts, values), pal.line),
        connectNulls: true,
        showSymbol: !longRange,
        symbolSize: 4,
        markArea: {
          silent: true,
          label: { show: false }, // named in the chart's HTML legend instead of over the line
          data: spo2Zones(scale).map((z) => [
            { yAxis: Math.max(z.from, floor), itemStyle: { color: pal.zone[z.points] } },
            { yAxis: z.to },
          ]),
        },
      },
    ],
  };
}

export function deltaOption(
  opts: { ts: number[]; values: Series; longRange: boolean; height: number; theme: ChartTheme } & Overlays,
): EChartsOption {
  const { ts, values, longRange, height, expected, anomalies, xMax } = opts;
  const pal = PALETTES[opts.theme];
  const base = frame(ts, longRange, height, pal, xMax);
  return {
    ...base,
    tooltip: { ...(base.tooltip as object), formatter: tooltipFormatter(" °C vs usual", 2, pal, { expected, anomalies }) },
    yAxis: yAxis(pal, { axisLabel: { color: pal.muted, fontSize: 12, formatter: (v: number) => `${v > 0 ? "+" : ""}${v}` } }),
    series: [
      ...overlaySeries(ts, pal, { expected, anomalies }),
      {
        ...line("Skin temp change", pairs(ts, values), pal.line),
        markLine: {
          silent: true,
          symbol: "none",
          label: { show: false },
          lineStyle: { color: pal.faint, type: "dashed" },
          data: [{ yAxis: 0 }],
        },
      },
    ],
  };
}

export function motionOption(
  opts: { ts: number[]; values: Series; longRange: boolean; height: number; theme: ChartTheme } & Pick<Overlays, "anomalies" | "xMax">,
): EChartsOption {
  const { ts, values, longRange, height, anomalies, xMax } = opts;
  const pal = PALETTES[opts.theme];
  const base = frame(ts, longRange, height, pal, xMax);
  return {
    ...base,
    tooltip: { ...(base.tooltip as object), formatter: tooltipFormatter(" counts", 0, pal, { anomalies }) },
    yAxis: yAxis(pal, { min: 0, scale: false }),
    series: [
      ...overlaySeries(ts, pal, { anomalies }),
      {
        name: "Movement",
        type: "bar",
        data: pairs(ts, values),
        barMaxWidth: 6,
        itemStyle: { color: pal.motion, borderRadius: [2, 2, 0, 0] },
      },
    ],
  };
}

export function inBedOption(
  opts: { ts: number[]; values: Series; longRange: boolean; height: number; xMax?: number; theme: ChartTheme },
): EChartsOption {
  const { ts, values, longRange, height, xMax } = opts;
  const pal = PALETTES[opts.theme];
  const base = frame(ts, longRange, height, pal, xMax);
  return {
    ...base,
    grid: { left: 52, right: 16, top: 4, bottom: 26 },
    xAxis: { ...(base.xAxis as object), axisLabel: { show: true, color: pal.muted, fontSize: 12, hideOverlap: true, formatter: (v: number) => (longRange ? facilityDayHHMM(v) : facilityHHMM(v)) } },
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
        areaStyle: { color: pal.bed },
      },
    ],
  };
}
