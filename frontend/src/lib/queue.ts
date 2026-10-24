// Join the compact /ws/live queue with the roster (names, rooms, code status) for the queue UI.
import type { Kpis, QueueMessage, RosterEntry, Tier, Unit, WireCard } from "@/lib/types";

export type QueueRow = WireCard & { rank: number; resident: RosterEntry | undefined };

export function queueRows(queue: QueueMessage | null, roster: Record<number, RosterEntry>): QueueRow[] {
  if (!queue) return [];
  return queue.items.map((c, i) => ({ ...c, rank: i + 1, resident: roster[c.id] }));
}

/** Reuse last render's row object for every card whose content and rank didn't change, so memoized
 *  cards skip re-rendering. `cache` is keyed by resident id and updated in place. */
export function reuseRows(cache: Map<number, { sig: string; row: QueueRow }>, rows: QueueRow[]): QueueRow[] {
  return rows.map((row) => {
    const { resident, ...card } = row;
    const sig = `${resident?.name ?? ""}|${JSON.stringify(card)}`;
    const hit = cache.get(row.id);
    if (hit && hit.sig === sig) return hit.row;
    cache.set(row.id, { sig, row });
    return row;
  });
}

export function splitFlagged(rows: QueueRow[]): { flagged: QueueRow[]; stable: QueueRow[] } {
  const flagged: QueueRow[] = [];
  const stable: QueueRow[] = [];
  for (const r of rows) (r.tier === "STABLE" ? stable : flagged).push(r);
  return { flagged, stable };
}

export const UNITS: { unit: Unit; label: string; first: number }[] = [
  { unit: "Memory Care", label: "Memory Care", first: 301 },
  { unit: "East", label: "East · long-term care", first: 101 },
  { unit: "West", label: "West · post-acute rehab", first: 201 },
];

export const ROOMS_PER_UNIT = 40;

export type RoomCell = { room: string; row: QueueRow | undefined };

/** Rooms 101–140, 201–240, 301–340 in order, each with its resident's queue row (if any). */
export function floorRooms(rows: QueueRow[]): Record<Unit, RoomCell[]> {
  const byRoom = new Map<string, QueueRow>();
  for (const r of rows) if (r.resident) byRoom.set(r.resident.room, r);
  const out = {} as Record<Unit, RoomCell[]>;
  for (const u of UNITS) {
    out[u.unit] = Array.from({ length: ROOMS_PER_UNIT }, (_, i) => {
      const room = String(u.first + i);
      return { room, row: byRoom.get(room) };
    });
  }
  return out;
}

/** Residents who just entered CRITICAL (for the floor-grid highlight); kept pure for tests. */
export function newlyCritical(prev: Map<number, Tier>, rows: QueueRow[]): number[] {
  return rows.filter((r) => r.tier === "CRITICAL" && prev.get(r.id) !== "CRITICAL").map((r) => r.id);
}

// ------------------------------------------------------------------ calm re-ranking (Phase 11)

export const HOLD_MS = 5000;

/** The order cards are shown in, and when it last changed. */
export type HeldOrder = { ids: number[]; since: number };

const same = (a: number[], b: number[]) => a.length === b.length && a.every((x, i) => x === b[i]);

/**
 * Cards keep their place for at least `holdMs` after the last re-rank, so the list doesn't shuffle under a
 * nurse's finger. Residents who leave drop out and new ones join at the end while held. A resident who is
 * CRITICAL and ranked higher than where they are shown re-ranks at once. Returns `prev` itself when nothing
 * changes (safe to compare by identity).
 */
export function holdOrder(prev: HeldOrder | null, ranked: number[], critical: Set<number>, now: number,
  holdMs = HOLD_MS): HeldOrder {
  if (prev === null) return { ids: ranked, since: now };
  if (same(prev.ids, ranked)) return prev;
  const urgent = ranked.some((id, i) => {
    if (!critical.has(id)) return false;
    const shown = prev.ids.indexOf(id);
    return shown === -1 || shown > i;
  });
  if (urgent || now - prev.since >= holdMs) return { ids: ranked, since: now };
  const live = new Set(ranked);
  const kept = [...prev.ids.filter((id) => live.has(id)), ...ranked.filter((id) => !prev.ids.includes(id))];
  return same(kept, prev.ids) ? prev : { ids: kept, since: prev.since };
}

/** `rows` in the held order. */
export function arrange<T extends { id: number }>(rows: T[], ids: number[]): T[] {
  const byId = new Map(rows.map((r) => [r.id, r]));
  return ids.map((id) => byId.get(id)).filter((r): r is T => r !== undefined);
}

/** Facility summary in words: "1 to check soon · 2 to keep an eye on · 117 resting as usual · …". */
export function summaryParts(kpis: Kpis): { n: number; text: string }[] {
  const t = kpis.tiers;
  const parts = [
    { n: t.CRITICAL, text: "to go to now" },
    { n: t.HIGH, text: "to check soon" },
    { n: t.WATCH, text: "to keep an eye on" },
    { n: t.STABLE, text: "resting as usual" },
    { n: kpis.devices_offline, text: kpis.devices_offline === 1 ? "device task" : "device tasks" },
    { n: kpis.overdue_checks, text: kpis.overdue_checks === 1 ? "round overdue" : "rounds overdue" },
  ];
  // "0 to check soon" only when nothing is more urgent; "N resting as usual" always.
  return parts.filter((p, i) => p.n > 0 || (i === 1 && t.CRITICAL === 0) || i === 3);
}

/** The alarm-fatigue state the gate put the card in, in words (null when neither applies). */
export function quietNote(row: QueueRow): string | null {
  if (row.rule?.ack) return "Check logged: this alert is acknowledged.";
  if (row.suppressed) return "Quiet for 30 min after a check, unless things get worse.";
  return null;
}
