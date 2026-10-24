// The replay clock (SPEC §4): the backend moves the cursor one step (2 min of replay time) at a time; between
// steps the client runs the clock forward at the playback speed so the header ticks smoothly.
import type { ReplayBrief } from "@/lib/types";

/** Replay time (epoch ms) `now` ms after a brief arrived at `receivedAt`. Never runs past the next step. */
export function replayClockMs(brief: ReplayBrief, receivedAt: number, now: number): number {
  const cursor = Date.parse(brief.cursor);
  if (!brief.playing) return cursor;
  const ahead = Math.max(0, now - receivedAt) * brief.speed;
  return Math.min(cursor + ahead, cursor + brief.step_s * 1000, Date.parse(brief.end));
}

/** 0–1 position of the cursor in the night (for the progress bar). */
export function replayProgress(brief: Pick<ReplayBrief, "start" | "end">, cursorMs: number): number {
  const s = Date.parse(brief.start);
  const e = Date.parse(brief.end);
  return e > s ? Math.min(1, Math.max(0, (cursorMs - s) / (e - s))) : 0;
}
