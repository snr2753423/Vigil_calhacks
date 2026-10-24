import { create } from "zustand";

import {
  applyMessage,
  initialLiveState,
  type ConnectionStatus,
  type LiveMessage,
  type LiveState,
} from "@/lib/live";

type LiveStore = LiveState & {
  setStatus: (status: ConnectionStatus) => void;
  receive: (msg: LiveMessage, receivedAt: number) => void;
};

export const useLiveStore = create<LiveStore>()((set) => ({
  ...initialLiveState,
  setStatus: (status) => set({ status }),
  receive: (msg, receivedAt) => set((state) => applyMessage(state, msg, receivedAt)),
}));
