import { describe, expect, it } from "vitest";

import { applyMessage, initialLiveState, MAX_TOASTS, type LiveState } from "@/lib/live";
import type { AlertMessage, QueueMessage, Tier, WireCard } from "@/lib/types";

const alert = (rid: number, ts: string, tier: Tier = "CRITICAL"): AlertMessage => ({
  type: "alert",
  ts,
  resident_id: rid,
  name: `R ${rid}`,
  room: "101",
  unit: "East",
  tier,
  from_tier: "STABLE",
  score: 100,
  rule: null,
  new_rules: [],
  factors: [],
  sound: tier === "CRITICAL",
});

const card = (id: number, tier: Tier): WireCard => ({
  id, score: 10, tier, rule: null, device: null, factors: [], last_check: null, check_min: null,
  suppressed: false, in_bed: null, worn: null, hr: null, rr: null, spo2: null,
});

const queue = (cards: WireCard[]): QueueMessage => ({
  type: "queue", as_of: "", display_time: null, night: false, mode: "live", eval_ms: 1,
  kpis: { tiers: { CRITICAL: 0, HIGH: 0, WATCH: 0, STABLE: 0 }, overdue_checks: 0, devices_offline: 0 },
  items: cards,
});

const step = (s: LiveState, m: Parameters<typeof applyMessage>[1]) => ({ ...s, ...applyMessage(s, m, 1) });

describe("alert toasts", () => {
  it("dedupes, replaces per resident and caps the stack", () => {
    let s = step(initialLiveState, alert(1, "a"));
    s = step(s, alert(1, "a"));
    expect(s.alerts).toHaveLength(1);
    s = step(s, alert(1, "b"));
    expect(s.alerts.map((a) => a.key)).toEqual(["1:b"]);
    for (let i = 2; i < 10; i++) s = step(s, alert(i, "x"));
    expect(s.alerts).toHaveLength(MAX_TOASTS);
    expect(s.alerts[0].resident_id).toBe(9);
  });

  it("clears a toast when a check is logged for that resident", () => {
    let s = step(step(initialLiveState, alert(1, "a")), alert(2, "a"));
    s = step(s, {
      type: "event",
      ts: "",
      event: { ts: "", type: "check_logged", staff_id: 3, payload: {}, resident_id: 1 },
    });
    expect(s.alerts.map((a) => a.resident_id)).toEqual([2]);
    expect(s.lastEvent?.event.resident_id).toBe(1);
  });

  it("drops toasts for residents no longer HIGH or CRITICAL", () => {
    let s = step(step(initialLiveState, alert(1, "a")), alert(2, "a", "HIGH"));
    s = step(s, queue([card(1, "WATCH"), card(2, "HIGH")]));
    expect(s.alerts.map((a) => a.resident_id)).toEqual([2]);
    expect(s.queue?.items).toHaveLength(2);
  });
});
