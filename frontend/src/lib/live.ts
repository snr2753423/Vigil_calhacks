// Types and pure helpers for the /ws/live stream (server → client).
import type {
  AlertMessage,
  DeviceMessage,
  EventMessage,
  InjectionMessage,
  InsightMessage,
  QueueMessage,
  SimState,
} from "@/lib/types";

export type { SimState } from "@/lib/types";

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

export type SimMessage = { type: "sim"; ts: string; sim: SimState };

export type LiveMessage =
  | HelloMessage
  | HeartbeatMessage
  | QueueMessage
  | AlertMessage
  | SimMessage
  | InjectionMessage
  | EventMessage
  | InsightMessage
  | DeviceMessage;

/** An alert waiting in the toast stack. */
export type ToastAlert = AlertMessage & { key: string; receivedAt: number };

export type ConnectionStatus = "connecting" | "open" | "closed";

export type ReceivedHeartbeat = HeartbeatMessage & { receivedAt: number };

export type LiveState = {
  status: ConnectionStatus;
  sim: SimState | null;
  heartbeatIntervalS: number | null;
  lastHeartbeat: ReceivedHeartbeat | null;
  /** Client-side gaps between consecutive heartbeats (ms), newest last. */
  intervalsMs: number[];
  queue: QueueMessage | null;
  queueReceivedAt: number | null;
  /** HIGH/CRITICAL alerts not yet dismissed, newest first. */
  alerts: ToastAlert[];
  lastEvent: EventMessage | null;
  lastInjection: InjectionMessage | null;
  lastInsight: InsightMessage | null;
  /** Newest live device reading per `${resident_id}:${device}`, stamped with when it arrived. */
  devices: Record<string, DeviceMessage & { receivedAt: number }>;
  /** Live device heart rate per resident: [epoch ms, bpm], the last LIVE_HR_WINDOW_MS. */
  liveHr: Record<number, [number, number][]>;
};

export const MAX_INTERVALS = 30;
export const LIVE_HR_WINDOW_MS = 10 * 60_000;
export const MAX_TOASTS = 4;

export const initialLiveState: LiveState = {
  status: "connecting",
  sim: null,
  heartbeatIntervalS: null,
  lastHeartbeat: null,
  intervalsMs: [],
  queue: null,
  queueReceivedAt: null,
  alerts: [],
  lastEvent: null,
  lastInjection: null,
  lastInsight: null,
  devices: {},
  liveHr: {},
};

const KNOWN_TYPES = new Set<LiveMessage["type"]>([
  "hello",
  "heartbeat",
  "queue",
  "alert",
  "sim",
  "injection",
  "event",
  "insight",
  "device",
]);

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
    case "sim":
      return { sim: msg.sim };
    case "queue": {
      // A toast for someone no longer HIGH/CRITICAL is stale: drop it.
      const flagged = new Set(msg.items.filter((c) => c.tier === "HIGH" || c.tier === "CRITICAL").map((c) => c.id));
      const alerts = state.alerts.filter((a) => flagged.has(a.resident_id));
      return alerts.length === state.alerts.length
        ? { queue: msg, queueReceivedAt: receivedAt }
        : { queue: msg, queueReceivedAt: receivedAt, alerts };
    }
    case "alert": {
      const key = `${msg.resident_id}:${msg.ts}`;
      if (state.alerts.some((a) => a.key === key)) return {};
      // A newer alert for the same resident replaces the older toast.
      const rest = state.alerts.filter((a) => a.resident_id !== msg.resident_id);
      return { alerts: [{ ...msg, key, receivedAt }, ...rest].slice(0, MAX_TOASTS) };
    }
    case "injection":
      return { lastInjection: msg };
    case "insight":
      return { lastInsight: msg };
    case "device": {
      const devices = { ...state.devices, [`${msg.resident_id}:${msg.device}`]: { ...msg, receivedAt } };
      if (msg.device !== "polar" || typeof msg.hr !== "number") return { devices };
      const t = Date.parse(msg.reading_ts);
      const trail = (state.liveHr[msg.resident_id] ?? []).filter(([at]) => at > t - LIVE_HR_WINDOW_MS && at < t);
      return { devices, liveHr: { ...state.liveHr, [msg.resident_id]: [...trail, [t, msg.hr]] } };
    }
    case "event": {
      // A logged check answers that resident's alert.
      const rid = msg.event.resident_id;
      return msg.event.type === "check_logged"
        ? { lastEvent: msg, alerts: state.alerts.filter((a) => a.resident_id !== rid) }
        : { lastEvent: msg };
    }
  }
}

export function dismissAlert(state: LiveState, key: string): Partial<LiveState> {
  return { alerts: state.alerts.filter((a) => a.key !== key) };
}
