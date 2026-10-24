"use client";

import { useNow } from "@/hooks/use-now";
import { replayClockMs } from "@/lib/replay";
import { useLiveStore } from "@/store/live";

export type Clock = {
  /** Epoch ms the UI should treat as "now": the replay cursor (run forward between steps) or wall time. */
  now: number | null;
  replay: boolean;
  /** The demo's display-clock offset (SPEC §4); 0 during a replay, which shows last night's real times. */
  offsetS: number;
};

/** One clock for headers, "minutes since check" and chart ranges: live wall time, or the replay cursor. */
export function useClock(): Clock {
  const wall = useNow();
  const sim = useLiveStore((s) => s.sim);
  const replay = useLiveStore((s) => s.replay);
  if (sim?.mode === "replay" && replay) {
    return { now: wall === null ? null : replayClockMs(replay, replay.receivedAt, wall), replay: true, offsetS: 0 };
  }
  return { now: wall, replay: sim?.mode === "replay", offsetS: sim?.display_clock_offset_s ?? 0 };
}

/** The replay cursor exactly as the backend last scored it (epoch ms), or null in live mode. Charts and the
 *  resident detail reload from this, not from the smoothly running display clock. */
export function useReplayCursor(): number | null {
  return useLiveStore((s) => (s.sim?.mode === "replay" && s.replay ? Date.parse(s.replay.cursor) : null));
}
