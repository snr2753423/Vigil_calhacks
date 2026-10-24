import { describe, expect, it } from "vitest";

import { calibrationAt, CALIBRATION_STEPS, CALIBRATION_TOTAL_S, meterFill, summarize, vec } from "@/lib/bed";

describe("summarize", () => {
  it("returns RMS, peak and mean gravity of a second of samples", () => {
    const s = summarize([
      { a: { x: 0.3, y: 0, z: 0.4 }, g: { x: 0, y: 1, z: 9.7 } },
      { a: { x: 0, y: 0, z: 0 }, g: { x: 0, y: 1.2, z: 9.7 } },
    ]);
    expect(s?.peak_jolt).toBe(0.5);
    expect(s?.motion_energy).toBeCloseTo(Math.sqrt(0.125), 4);
    expect(s?.gravity).toEqual([0, 1.1, 9.7]);
  });

  it("estimates motion from gravity alone when linear acceleration is missing", () => {
    const s = summarize([
      { a: null, g: { x: 0, y: 0, z: 9.8 } },
      { a: null, g: { x: 0, y: 0, z: 10.2 } },
    ]);
    expect(s?.motion_energy).toBeCloseTo(0.2, 5);
    expect(s?.gravity).toEqual([0, 0, 10]);
  });

  it("handles no data and no gravity", () => {
    expect(summarize([])).toBeNull();
    expect(summarize([{ a: { x: 0.1, y: 0, z: 0 }, g: null }])).toEqual({ motion_energy: 0.1, peak_jolt: 0.1, gravity: null });
  });

  it("vec drops partial readings", () => {
    expect(vec({ x: 1, y: null, z: 2 })).toBeNull();
    expect(vec(null)).toBeNull();
    expect(vec({ x: 1, y: 2, z: 3 })).toEqual({ x: 1, y: 2, z: 3 });
  });
});

describe("calibration steps", () => {
  it("walks prep → empty → prep → still → shift, then done", () => {
    expect(CALIBRATION_STEPS.map((s) => s.phase)).toEqual(["prep", "empty", "prep", "still", "shift"]);
    expect(CALIBRATION_STEPS.find((s) => s.phase === "still")?.seconds).toBe(10);
    expect(calibrationAt(0)).toEqual({ index: 0, remainingS: 8 });
    expect(calibrationAt(8)).toEqual({ index: 1, remainingS: 5 });
    expect(calibrationAt(12.2)).toEqual({ index: 1, remainingS: 1 });
    expect(calibrationAt(19)).toEqual({ index: 3, remainingS: 10 });
    expect(calibrationAt(CALIBRATION_TOTAL_S - 0.5)).toEqual({ index: 4, remainingS: 1 });
    expect(calibrationAt(CALIBRATION_TOTAL_S)).toBeNull();
  });
});

describe("meterFill", () => {
  it("is 0 below a tenth of quiet, about 0.4 at quiet, full for big jolts", () => {
    expect(meterFill(0.001, 0.05)).toBe(0);
    expect(meterFill(0.05, 0.05)).toBeCloseTo(0.4, 5);
    expect(meterFill(5, 0.05)).toBe(1);
    expect(meterFill(0, 0.05)).toBe(0);
  });
});
