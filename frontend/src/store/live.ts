import { create } from "zustand";

import {
  applyMessage,
  dismissAlert,
  initialLiveState,
  type ConnectionStatus,
  type LiveMessage,
  type LiveState,
} from "@/lib/live";
import type { RosterEntry } from "@/lib/types";

type LiveStore = LiveState & {
  /** GET /api/residents, keyed by resident_id (names, rooms, code status for the compact queue). */
  roster: Record<number, RosterEntry>;
  setStatus: (status: ConnectionStatus) => void;
  receive: (msg: LiveMessage, receivedAt: number) => void;
  dismiss: (key: string) => void;
  setRoster: (rows: RosterEntry[]) => void;
};

export const useLiveStore = create<LiveStore>()((set) => ({
  ...initialLiveState,
  roster: {},
  setStatus: (status) => set({ status }),
  receive: (msg, receivedAt) => set((state) => applyMessage(state, msg, receivedAt)),
  dismiss: (key) => set((state) => dismissAlert(state, key)),
  setRoster: (rows) => set({ roster: Object.fromEntries(rows.map((r) => [r.resident_id, r])) }),
}));
