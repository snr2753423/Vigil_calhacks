// Types and pure helpers for the /ws/live stream (server → client).

export type SimState = {
  mode: "live" | "replay";
  paused: boolean;
  force_night: boolean;
  display_clock_offset_s: number;
};

export type HelloMessage = {
  type: "hello";
  ts: string;
  heartbeat_interval_s: number;
  sim: SimState;
};

export type HeartbeatMessage = {
  type: "heartbeat";
  seq: number;
  ts: string;
  clients: number;
  sim: SimState;
};

export type LiveMessage = HelloMessage | HeartbeatMessage;

export type ConnectionStatus = "connecting" | "open" | "closed";

export type ReceivedHeartbeat = HeartbeatMessage & { receivedAt: number };

export type LiveState = {
  status: ConnectionStatus;
  sim: SimState | null;
  heartbeatIntervalS: number | null;
  lastHeartbeat: ReceivedHeartbeat | null;
  /** Client-side gaps between consecutive heartbeats (ms), newest last. */
  intervalsMs: number[];
};

export const MAX_INTERVALS = 30;

export const initialLiveState: LiveState = {
  status: "connecting",
  sim: null,
  heartbeatIntervalS: null,
  lastHeartbeat: null,
  intervalsMs: [],
};

const KNOWN_TYPES = new Set<LiveMessage["type"]>(["hello", "heartbeat"]);

export function liveSocketUrl(
  loc: Pick<Location, "protocol" | "host">,
  path = "/ws/live",
): string {
  return `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}${path}`;
}

export function reconnectDelayMs(attempt: number): number {
  return Math.min(500 * 2 ** attempt, 5000);
}

/** An open socket that has been silent for 3 heartbeats (min 5 s) is presumed dead (half-open, dropped). */
export function isStale(lastMessageAt: number, now: number, heartbeatIntervalS: number | null): boolean {
  return now - lastMessageAt > Math.max(3 * (heartbeatIntervalS ?? 2) * 1000, 5000);
}

export function parseLiveMessage(raw: string): LiveMessage | null {
  try {
    const msg: unknown = JSON.parse(raw);
    if (
      typeof msg === "object" &&
      msg !== null &&
      KNOWN_TYPES.has((msg as { type: LiveMessage["type"] }).type)
    ) {
      return msg as LiveMessage;
    }
  } catch {
    // ignore malformed frames
  }
  return null;
}

export function applyMessage(
  state: LiveState,
  msg: LiveMessage,
  receivedAt: number,
): Partial<LiveState> {
  switch (msg.type) {
    case "hello":
      return { sim: msg.sim, heartbeatIntervalS: msg.heartbeat_interval_s };
    case "heartbeat": {
      const prev = state.lastHeartbeat;
      const consecutive = prev !== null && msg.seq === prev.seq + 1;
      return {
        sim: msg.sim,
        lastHeartbeat: { ...msg, receivedAt },
        intervalsMs: consecutive
          ? [...state.intervalsMs, receivedAt - prev.receivedAt].slice(-MAX_INTERVALS)
          : state.intervalsMs,
      };
    }
  }
}
