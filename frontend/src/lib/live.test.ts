import { describe, expect, it } from "vitest";

import { formatFacilityClock } from "@/lib/facility";
import {
  MAX_INTERVALS,
  applyMessage,
  initialLiveState,
  isStale,
  liveSocketUrl,
  parseLiveMessage,
  reconnectDelayMs,
  type HeartbeatMessage,
  type LiveState,
} from "@/lib/live";

const sim = { mode: "live", paused: false, force_night: false, display_clock_offset_s: 0 } as const;
const hb = (seq: number): HeartbeatMessage => ({
  type: "heartbeat",
  seq,
  ts: "2026-10-16T09:14:00.000Z",
  clients: 1,
  sim,
});

describe("liveSocketUrl", () => {
  it("uses ws on http and wss on https, same host", () => {
    expect(liveSocketUrl({ protocol: "http:", host: "localhost:8080" })).toBe("ws://localhost:8080/ws/live");
    expect(liveSocketUrl({ protocol: "https:", host: "x.trycloudflare.com" })).toBe(
      "wss://x.trycloudflare.com/ws/live",
    );
  });
});

describe("reconnectDelayMs", () => {
  it("backs off exponentially and caps at 5 s", () => {
    expect([0, 1, 2, 3, 4, 10].map(reconnectDelayMs)).toEqual([500, 1000, 2000, 4000, 5000, 5000]);
  });
});

describe("isStale", () => {
  it("allows 3 heartbeats of silence (at least 5 s) before declaring the socket dead", () => {
    expect(isStale(0, 5_000, 2)).toBe(false);
    expect(isStale(0, 6_001, 2)).toBe(true);
    expect(isStale(0, 4_999, 1)).toBe(false); // floor of 5 s
    expect(isStale(0, 5_001, null)).toBe(false); // default 2 s interval -> 6 s
  });
});

describe("parseLiveMessage", () => {
  it("accepts known types and rejects junk", () => {
    expect(parseLiveMessage(JSON.stringify(hb(1)))?.type).toBe("heartbeat");
    expect(parseLiveMessage('{"type":"nope"}')).toBeNull();
    expect(parseLiveMessage("not json")).toBeNull();
  });
});

describe("applyMessage", () => {
  it("records hello settings", () => {
    const next = applyMessage(
      initialLiveState,
      { type: "hello", ts: "", heartbeat_interval_s: 2, sim },
      0,
    );
    expect(next).toMatchObject({ heartbeatIntervalS: 2, sim });
  });

  it("tracks gaps only between consecutive heartbeats", () => {
    let state: LiveState = { ...initialLiveState };
    state = { ...state, ...applyMessage(state, hb(1), 1000) };
    expect(state.intervalsMs).toEqual([]);
    state = { ...state, ...applyMessage(state, hb(2), 3010) };
    expect(state.intervalsMs).toEqual([2010]);
    state = { ...state, ...applyMessage(state, hb(7), 5000) }; // server restarted / missed beats
    expect(state.intervalsMs).toEqual([2010]);
    expect(state.lastHeartbeat?.seq).toBe(7);
  });

  it("keeps at most MAX_INTERVALS gaps", () => {
    let state: LiveState = { ...initialLiveState };
    for (let seq = 1; seq <= MAX_INTERVALS + 5; seq++) {
      state = { ...state, ...applyMessage(state, hb(seq), seq * 2000) };
    }
    expect(state.intervalsMs).toHaveLength(MAX_INTERVALS);
  });

  it("keeps the newest Cortex insight so the resident page can reload", () => {
    const raw = JSON.stringify({
      type: "insight", kind: "assessment", resident_id: 17, as_of: "2026-10-17T09:14:00Z", live: true,
      headline: "Check Rosa now", model: "claude-sonnet-5",
    });
    const msg = parseLiveMessage(raw);
    expect(msg?.type).toBe("insight");
    const state = { ...initialLiveState, ...applyMessage(initialLiveState, msg!, 1) };
    expect(state.lastInsight?.resident_id).toBe(17);
  });
});

describe("formatFacilityClock", () => {
  it("shows facility time and honors the display offset", () => {
    const t = Date.parse("2026-10-16T09:14:05Z"); // 02:14:05 PDT
    expect(formatFacilityClock(t)).toBe("02:14:05");
    expect(formatFacilityClock(t, 3600)).toBe("03:14:05");
  });
});
