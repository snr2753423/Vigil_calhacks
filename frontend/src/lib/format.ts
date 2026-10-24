import { FACILITY_TZ } from "@/lib/facility";

const hhmm = new Intl.DateTimeFormat("en-US", {
  timeZone: FACILITY_TZ,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const dayHhmm = new Intl.DateTimeFormat("en-US", {
  timeZone: FACILITY_TZ,
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const hourFmt = new Intl.DateTimeFormat("en-US", { timeZone: FACILITY_TZ, hour: "numeric", hourCycle: "h23" });

/** Facility-local HH:MM of an epoch-ms instant, shifted by the demo's display offset (SPEC §4). */
export function facilityHHMM(epochMs: number, offsetS = 0): string {
  return hhmm.format(new Date(epochMs + offsetS * 1000));
}

export function facilityDayHHMM(epochMs: number, offsetS = 0): string {
  return dayHhmm.format(new Date(epochMs + offsetS * 1000));
}

/** 0–23 facility-local hour (Intl may print "24" at midnight in some engines). */
export function facilityHour(epochMs: number): number {
  return Number(hourFmt.format(new Date(epochMs))) % 24;
}

/** "45 min", "2 h 05 min", "3 d". */
export function formatMinutes(min: number): string {
  const m = Math.max(0, Math.round(min));
  if (m < 60) return `${m} min`;
  if (m < 48 * 60) {
    const h = Math.floor(m / 60);
    const rest = m % 60;
    return rest ? `${h} h ${String(rest).padStart(2, "0")} min` : `${h} h`;
  }
  return `${Math.round(m / 1440)} d`;
}

/** Minutes between an ISO timestamp and now (null if no timestamp). */
export function minutesSince(iso: string | null, nowMs: number): number | null {
  if (!iso) return null;
  return Math.max(0, (nowMs - Date.parse(iso)) / 60000);
}
