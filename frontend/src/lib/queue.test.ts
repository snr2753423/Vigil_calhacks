import { describe, expect, it } from "vitest";

import { floorRooms, queueRows, reuseRows, splitFlagged } from "@/lib/queue";
import type { QueueMessage, RosterEntry, Tier, WireCard } from "@/lib/types";

const card = (id: number, tier: Tier): WireCard => ({
  id, score: 50, tier, rule: null, device: null, factors: [], last_check: null, check_min: null,
  suppressed: false, in_bed: null, worn: null, hr: null, rr: null, spo2: null,
});
const resident = (id: number, room: string): RosterEntry =>
  ({ resident_id: id, name: `N${id}`, room, unit: "East" }) as RosterEntry;

const q = { items: [card(17, "CRITICAL"), card(3, "WATCH"), card(5, "STABLE")] } as QueueMessage;

describe("queue rows", () => {
  it("ranks by position and joins the roster", () => {
    const rows = queueRows(q, { 17: resident(17, "301"), 5: resident(5, "105") });
    expect(rows.map((r) => [r.id, r.rank, r.resident?.room])).toEqual([
      [17, 1, "301"],
      [3, 2, undefined],
      [5, 3, "105"],
    ]);
    const { flagged, stable } = splitFlagged(rows);
    expect(flagged.map((r) => r.id)).toEqual([17, 3]);
    expect(stable.map((r) => r.id)).toEqual([5]);
  });

  it("lays out 40 rooms per unit and maps residents to rooms", () => {
    const rooms = floorRooms(queueRows(q, { 17: resident(17, "301"), 5: resident(5, "105") }));
    expect(rooms.East).toHaveLength(40);
    expect(rooms.East[0].room).toBe("101");
    expect(rooms["Memory Care"][39].room).toBe("340");
    expect(rooms.East[4].row?.id).toBe(5);
    expect(rooms["Memory Care"][0].row?.tier).toBe("CRITICAL");
  });
});

describe("reuseRows", () => {
  it("keeps the same object for an unchanged card so memoized cards skip re-rendering", () => {
    const cache = new Map();
    const roster = { 17: resident(17, "301") };
    const a = reuseRows(cache, queueRows(q, roster));
    const b = reuseRows(cache, queueRows(q, roster));
    expect(b[0]).toBe(a[0]);
    const changed = { items: [{ ...card(17, "CRITICAL"), score: 99 }, ...q.items.slice(1)] } as QueueMessage;
    const c = reuseRows(cache, queueRows(changed, roster));
    expect(c[0]).not.toBe(a[0]);
    expect(c[1]).toBe(a[1]);
  });
});
