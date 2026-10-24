// The demo's display offset (SPEC §4): make the header clock read a chosen facility time.
import { FACILITY_TZ } from "@/lib/facility";

const parts = new Intl.DateTimeFormat("en-US", {
  timeZone: FACILITY_TZ,
  hour: "numeric",
  minute: "numeric",
  second: "numeric",
  hourCycle: "h23",
});

/** Seconds to add to real time so the facility clock reads `hhmm` ("02:14"), within ±12 h. */
export function offsetForDisplayTime(hhmm: string, nowMs: number): number {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
  if (!m) throw new Error("Use HH:MM");
  const target = (Number(m[1]) % 24) * 3600 + Number(m[2]) * 60;
  const p = Object.fromEntries(parts.formatToParts(new Date(nowMs)).map((x) => [x.type, x.value]));
  const current = (Number(p.hour) % 24) * 3600 + Number(p.minute) * 60 + Number(p.second);
  let diff = target - current;
  if (diff > 43200) diff -= 86400;
  if (diff < -43200) diff += 86400;
  return Math.round(diff);
}
