export const FACILITY_NAME = "Sunrise Gardens Skilled Nursing Facility";
export const FACILITY_SHORT_NAME = "Sunrise Gardens";
export const FACILITY_TZ = "America/Los_Angeles";

const clockFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: FACILITY_TZ,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** Facility wall-clock time for display; `offsetS` is the sim's display_clock_offset_s (SPEC §4). */
export function formatFacilityClock(epochMs: number, offsetS = 0): string {
  return clockFormat.format(new Date(epochMs + offsetS * 1000));
}
