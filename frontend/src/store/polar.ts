// Polar Verity Sense → Mac Chrome (Web Bluetooth) → /ws/ingest (SPEC §10).
//
// The Bluetooth connection and the ingest socket live at module level, not in a page, so the band keeps
// streaming while you move between Rounds, R-120's chart and Admin. A full page reload drops them (Chrome
// only lets a click start a Bluetooth connection), so reconnect from /devices after a reload.

import { create } from "zustand";

import {
  bleReconnectDelayMs,
  HEART_RATE_MEASUREMENT,
  HEART_RATE_SERVICE,
  ingestMessage,
  parseHeartRateMeasurement,
  POLAR_PMD_SERVICE,
  wornFrom,
} from "@/lib/ble";
import { IngestSocket, type IngestStatus } from "@/lib/ingest-socket";

// Minimal Web Bluetooth typings (not in TypeScript's DOM lib).
type BleCharacteristic = EventTarget & { value: DataView | null; startNotifications(): Promise<unknown> };
type BleServer = {
  getPrimaryService(service: string): Promise<{ getCharacteristic(c: string): Promise<BleCharacteristic> }>;
};
type BleDevice = EventTarget & {
  id: string;
  name?: string;
  gatt?: { connected: boolean; connect(): Promise<BleServer>; disconnect(): void };
};
type Bluetooth = { requestDevice(options: object): Promise<BleDevice> };

export type PolarStatus = "unsupported" | "idle" | "choosing" | "connecting" | "connected" | "reconnecting" | "error";

type PolarState = {
  status: PolarStatus;
  deviceName: string | null;
  residentId: number;
  hr: number | null;
  worn: boolean | null;
  contactSupported: boolean | null;
  lastAt: number | null;
  readings: number;
  /** Readings the ingest socket accepted for sending (it drops them while disconnected). */
  sent: number;
  ingest: IngestStatus | "off";
  /** Why the backend isn't storing the readings (e.g. the simulated band is on), from its reply. */
  rejected: string | null;
  error: string | null;
  reconnectAttempt: number;
  connect: (residentId: number) => Promise<void>;
  disconnect: () => void;
};

let device: BleDevice | null = null;
let socket: IngestSocket | null = null;
let manual = false;
let retryTimer: ReturnType<typeof setTimeout> | undefined;

export function bluetooth(): Bluetooth | undefined {
  if (typeof navigator === "undefined") return undefined;
  return (navigator as Navigator & { bluetooth?: Bluetooth }).bluetooth;
}

export const usePolar = create<PolarState>()((set, get) => {
  const onValue = (e: Event) => {
    const value = (e.target as BleCharacteristic).value;
    if (!value) return;
    let m;
    try {
      m = parseHeartRateMeasurement(value);
    } catch {
      return;
    }
    const now = Date.now();
    const ok = socket?.send(ingestMessage(m, get().residentId, now)) ?? false;
    set((s) => ({
      hr: m.hr > 0 ? m.hr : null,
      worn: wornFrom(m),
      contactSupported: m.contactSupported,
      lastAt: now,
      readings: s.readings + 1,
      sent: s.sent + (ok ? 1 : 0),
    }));
  };

  const attach = async (d: BleDevice) => {
    if (!d.gatt) throw new Error("This device has no GATT server");
    const server = await d.gatt.connect();
    const service = await server.getPrimaryService(HEART_RATE_SERVICE);
    const ch = await service.getCharacteristic(HEART_RATE_MEASUREMENT);
    ch.addEventListener("characteristicvaluechanged", onValue);
    await ch.startNotifications();
    set({ status: "connected", error: null, reconnectAttempt: 0 });
  };

  const reconnect = () => {
    clearTimeout(retryTimer);
    if (manual || !device) return;
    const attempt = get().reconnectAttempt;
    set({ status: "reconnecting" });
    retryTimer = setTimeout(async () => {
      if (manual || !device) return;
      try {
        await attach(device);
      } catch (e) {
        set({ reconnectAttempt: attempt + 1, error: e instanceof Error ? e.message : String(e) });
        reconnect();
      }
    }, bleReconnectDelayMs(attempt));
  };

  const onDisconnected = () => {
    set({ hr: null });
    reconnect(); // the band went out of range or slept: keep trying until Disconnect is pressed
  };

  return {
    status: "idle", // the /devices page checks for Web Bluetooth after mount (no SSR mismatch)
    deviceName: null,
    residentId: 120,
    hr: null,
    worn: null,
    contactSupported: null,
    lastAt: null,
    readings: 0,
    sent: 0,
    ingest: "off",
    rejected: null,
    error: null,
    reconnectAttempt: 0,

    connect: async (residentId: number) => {
      const bt = bluetooth();
      if (!bt) {
        set({ status: "unsupported" });
        return;
      }
      set({ status: "choosing", error: null, residentId });
      let picked: BleDevice;
      try {
        picked = await bt.requestDevice({
          filters: [{ services: [HEART_RATE_SERVICE] }],
          optionalServices: [POLAR_PMD_SERVICE],
        });
      } catch (e) {
        // NotFoundError = the chooser was closed without picking a band
        const cancelled = e instanceof DOMException && e.name === "NotFoundError";
        set({ status: device ? get().status : "idle", error: cancelled ? null : String(e) });
        return;
      }
      get().disconnect();
      manual = false;
      device = picked;
      picked.addEventListener("gattserverdisconnected", onDisconnected);
      set({ status: "connecting", deviceName: picked.name ?? "Heart-rate band", readings: 0, sent: 0, rejected: null });
      socket = new IngestSocket({
        device: "polar",
        residentId,
        onStatus: (s) => set({ ingest: s }),
        onMessage: (msg) => {
          if (msg.type === "hello" || msg.type === "ack") {
            set({ rejected: msg.accepted === false ? String(msg.reason ?? "rejected") : null });
          } else if (msg.type === "error") {
            set({ error: String(msg.message) });
          }
        },
      });
      try {
        await attach(picked);
      } catch (e) {
        set({ error: e instanceof Error ? e.message : String(e) });
        reconnect();
      }
    },

    disconnect: () => {
      manual = true;
      clearTimeout(retryTimer);
      if (device) {
        device.removeEventListener("gattserverdisconnected", onDisconnected);
        if (device.gatt?.connected) device.gatt.disconnect();
      }
      device = null;
      socket?.close();
      socket = null;
      set({ status: bluetooth() ? "idle" : "unsupported", hr: null, ingest: "off", reconnectAttempt: 0 });
    },
  };
});
