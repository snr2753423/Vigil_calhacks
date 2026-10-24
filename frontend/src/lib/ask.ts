// Ask the facility: an ECharts option from a Cortex Analyst result's shape (the backend picks the chart type).
import type { EChartsOption } from "echarts";

import { type ChartPalette, type ChartTheme, PALETTES } from "@/lib/chart-options";
import { TIERS } from "@/lib/tiers";
import type { AskResult, Tier } from "@/lib/types";

/** Colour per series value: tier colours for tiers, else the theme's categorical palette. */
export function seriesColor(name: string, i: number, theme: ChartTheme = "night"): string {
  const tier = (TIERS as Record<string, { hex: string; dayHex: string }>)[name as Tier];
  if (tier) return theme === "day" ? tier.dayHex : tier.hex;
  const pal: ChartPalette = PALETTES[theme];
  return pal.series[i % pal.series.length];
}

export function prettyColumn(name: string): string {
  const s = name.toLowerCase().replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export type AskChartSpec = { option: EChartsOption; series: { name: string; color: string }[]; height: number };

export function askChartOption(r: AskResult, theme: ChartTheme = "night"): AskChartSpec | null {
  const pal = PALETTES[theme];
  const c = r.chart;
  if (!c || !r.rows.length) return null;
  const col = (name: string) => r.columns.findIndex((x) => x.name === name);
  const xi = col(c.x);
  const yi = col(c.y);
  if (xi < 0 || yi < 0) return null;
  const si = c.series ? col(c.series) : -1;
  const xs = [...new Set(r.rows.map((row) => String(row[xi])))];
  const num = (v: unknown) => (typeof v === "number" ? v : v === null ? null : Number(v));
  const groups = si >= 0 ? [...new Set(r.rows.map((row) => String(row[si])))] : [prettyColumn(c.y)];
  const series = groups.map((g, i) => {
    const byX = new Map<string, number | null>();
    for (const row of r.rows) if (si < 0 || String(row[si]) === g) byX.set(String(row[xi]), num(row[yi]));
    const color = si >= 0 ? seriesColor(g, i, theme) : pal.series[0];
    return {
      name: g,
      type: c.type,
      data: xs.map((x) => byX.get(x) ?? (c.type === "bar" ? 0 : null)),
      itemStyle: { color, borderRadius: c.type === "bar" ? [3, 3, 0, 0] : undefined },
      lineStyle: c.type === "line" ? { width: 2, color } : undefined,
      showSymbol: c.type === "line",
      symbolSize: 6,
      barMaxWidth: 28,
      stack: si >= 0 && c.type === "bar" ? "total" : undefined,
      connectNulls: true,
    };
  });
  const horizontal = c.type === "bar" && si < 0 && xs.length > 6;
  const height = horizontal ? Math.max(180, xs.length * 26 + 40) : 240;
  const cat = { type: "category" as const, data: xs, axisLabel: { color: pal.muted, fontSize: 12, interval: 0, hideOverlap: true }, axisLine: { lineStyle: { color: pal.axis } }, axisTick: { show: false } };
  const val = { type: "value" as const, axisLabel: { color: pal.muted, fontSize: 12 }, splitLine: { lineStyle: { color: pal.grid } }, scale: c.type === "line" };
  const option: EChartsOption = {
    animation: false,
    grid: { left: horizontal ? 140 : 52, right: 16, top: 12, bottom: horizontal ? 24 : 40 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: c.type === "bar" ? "shadow" : "line" },
      backgroundColor: pal.tooltipBg,
      borderColor: pal.tooltipBorder,
      textStyle: { color: pal.tooltipText, fontSize: 14 },
    },
    xAxis: horizontal ? val : cat,
    yAxis: horizontal ? { ...cat, inverse: true, axisLabel: { ...cat.axisLabel, width: 130, overflow: "truncate" as const } } : val,
    series: series as EChartsOption["series"],
  };
  return { option, series: si >= 0 ? series.map((s) => ({ name: s.name, color: s.itemStyle.color })) : [], height };
}

/** A table cell for display. */
export function formatCell(v: string | number | boolean | null, type: string): string {
  if (v === null) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") return type === "real" ? v.toLocaleString(undefined, { maximumFractionDigits: 1 }) : v.toLocaleString();
  if (type.startsWith("timestamp")) return v.replace("T", " ").slice(0, 16);
  return v;
}
