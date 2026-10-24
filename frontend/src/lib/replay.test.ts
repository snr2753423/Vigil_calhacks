import { describe, expect, it } from "vitest";

import { applyMessage, initialLiveState } from "@/lib/live";
import { replayClockMs, replayProgress } from "@/lib/replay";
import type { ReplayBrief, ReplayMessage } from "@/lib/types";

const brief: ReplayBrief = {
  cursor: "2026-10-17T08:44:00.000Z",
  speed: 60,
  playing: true,
  step_s: 120,
  start: "2026-10-17T05:00:00.000Z",
  end: "2026-10-17T13:30:00.000Z",
};
const cursor = Date.parse(brief.cursor);

describe("replay clock", () => {
  it("runs forward at the playback speed between steps", () => {
    expect(replayClockMs(brief, 1000, 1000)).toBe(cursor);
    expect(replayClockMs(brief, 1000, 2000)).toBe(cursor + 60_000); // 1 s wall at 60× = 1 min
  });
  it("never runs past the next step, the end of the night, or while paused", () => {
    expect(replayClockMs(brief, 1000, 9000)).toBe(cursor + 120_000);
    expect(replayClockMs({ ...brief, playing: false }, 1000, 5000)).toBe(cursor);
    const late = { ...brief, cursor: "2026-10-17T13:29:30.000Z" };
    expect(replayClockMs(late, 0, 5000)).toBe(Date.parse(brief.end));
  });
  it("progress through the night", () => {
    expect(replayProgress(brief, Date.parse(brief.start))).toBe(0);
    expect(replayProgress(brief, Date.parse(brief.end) + 1)).toBe(1);
  });
});

describe("replay messages", () => {
  const msg: ReplayMessage = { type: "replay", ts: "x", seeked: false, ...brief };
  const alert = {
    type: "alert" as const, ts: "t", resident_id: 17, name: "R", room: "317", unit: "Memory Care" as const,
    tier: "HIGH" as const, from_tier: "WATCH" as const, score: 63, rule: null, new_rules: [], factors: [], sound: false,
  };
  it("stores the brief with its arrival time", () => {
    const next = applyMessage(initialLiveState, msg, 42);
    expect(next.replay).toEqual({ ...brief, receivedAt: 42 });
  });
  it("a seek clears toasts from before the jump", () => {
    const withToast = { ...initialLiveState, alerts: [{ ...alert, key: "k", receivedAt: 1 }] };
    expect(applyMessage(withToast, msg, 2).alerts).toBeUndefined();
    expect(applyMessage(withToast, { ...msg, seeked: true }, 2).alerts).toEqual([]);
  });
  it("back to live drops the brief and the replay's toasts", () => {
    const replaying = {
      ...initialLiveState,
      sim: { mode: "replay" as const, paused: false, force_night: false, display_clock_offset_s: 0, replay: brief },
      replay: { ...brief, receivedAt: 1 },
      alerts: [{ ...alert, key: "k", receivedAt: 1 }],
    };
    const next = applyMessage(replaying, {
      type: "sim", ts: "x", sim: { mode: "live", paused: false, force_night: false, display_clock_offset_s: 0, replay: null },
    }, 5);
    expect(next.replay).toBeNull();
    expect(next.alerts).toEqual([]);
  });
});
