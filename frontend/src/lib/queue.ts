// Join the compact /ws/live queue with the roster (names, rooms, code status) for the queue UI.
import type { QueueMessage, RosterEntry, Tier, Unit, WireCard } from "@/lib/types";

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
  { unit: "East", label: "East · long-term care", first: 101 },
  { unit: "West", label: "West · post-acute rehab", first: 201 },
  { unit: "Memory Care", label: "Memory Care", first: 301 },
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
