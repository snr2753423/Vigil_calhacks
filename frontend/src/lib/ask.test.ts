import { describe, expect, it } from "vitest";

import { askChartOption, formatCell, prettyColumn } from "@/lib/ask";
import type { AskResult } from "@/lib/types";

const result = (over: Partial<AskResult>): AskResult => ({
  question: "q", interpretation: "", sql: "SELECT 1", verified_query: null, suggestions: [], warnings: [],
  request_id: null, analyst_ms: 1000, models: [], columns: [], rows: [], row_count: 0, truncated: false,
  chart: null, error: null, ...over,
});

describe("askChartOption", () => {
  it("stacks a grouped bar by its series column, coloured by tier", () => {
    const spec = askChartOption(result({
      columns: [{ name: "UNIT", type: "text" }, { name: "TIER", type: "text" }, { name: "ALERTS", type: "fixed" }],
      rows: [["East", "HIGH", 2], ["West", "CRITICAL", 8], ["West", "HIGH", 1]],
      chart: { type: "bar", x: "UNIT", y: "ALERTS", series: "TIER" },
    }))!;
    const series = spec.option.series as { name: string; data: number[]; stack: string }[];
    expect(series.map((s) => s.name)).toEqual(["HIGH", "CRITICAL"]);
    expect(series[0].data).toEqual([2, 1]); // East, West
    expect(series[1].data).toEqual([0, 8]);
    expect(spec.series[1].color).toBe("#e8836c"); // night CRITICAL
    const day = askChartOption(result({
      columns: [{ name: "UNIT", type: "text" }, { name: "TIER", type: "text" }, { name: "ALERTS", type: "fixed" }],
      rows: [["West", "CRITICAL", 8]],
      chart: { type: "bar", x: "UNIT", y: "ALERTS", series: "TIER" },
    }), "day")!;
    expect(day.series[0].color).toBe("#b4432f");
  });
  it("draws a line over dates", () => {
    const spec = askChartOption(result({
      columns: [{ name: "NIGHT_OF", type: "date" }, { name: "AVG_NIGHT_HEART_RATE", type: "real" }],
      rows: [["2026-10-15", 61.5], ["2026-10-16", 62.1]],
      chart: { type: "line", x: "NIGHT_OF", y: "AVG_NIGHT_HEART_RATE" },
    }))!;
    const s = (spec.option.series as { type: string; data: number[] }[])[0];
    expect(s.type).toBe("line");
    expect(s.data).toEqual([61.5, 62.1]);
  });
  it("turns a long category list horizontal", () => {
    const rows = Array.from({ length: 10 }, (_, i) => [`R${i}`, i] as [string, number]);
    const spec = askChartOption(result({
      columns: [{ name: "RESIDENT_NAME", type: "text" }, { name: "BED_EXITS", type: "fixed" }],
      rows, chart: { type: "bar", x: "RESIDENT_NAME", y: "BED_EXITS" },
    }))!;
    expect((spec.option.yAxis as { type: string }).type).toBe("category");
  });
  it("has nothing to draw without a chart or rows", () => {
    expect(askChartOption(result({}))).toBeNull();
  });
});

describe("cells and headers", () => {
  it("formats values by column type", () => {
    expect(formatCell(61.45, "real")).toBe("61.5");
    expect(formatCell(null, "text")).toBe("—");
    expect(formatCell("2026-10-17T02:15:00", "timestamp_ntz")).toBe("2026-10-17 02:15");
    expect(prettyColumn("AVG_NIGHT_HEART_RATE")).toBe("Avg night heart rate");
  });
});
