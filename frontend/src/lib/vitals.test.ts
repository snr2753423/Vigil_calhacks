import { describe, expect, it } from "vitest";

import { offsetForDisplayTime } from "@/lib/display-clock";
import { describeEvent } from "@/lib/events";
import { formatMinutes } from "@/lib/format";
import { baselineBand, deltaVsBaseline, spo2Zones, todBlock } from "@/lib/vitals";

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
