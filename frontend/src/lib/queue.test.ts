import { describe, expect, it } from "vitest";

import { arrange, floorRooms, holdOrder, queueRows, reuseRows, splitFlagged, summaryParts } from "@/lib/queue";
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

describe("calm re-ranking", () => {
  const none = new Set<number>();

  it("holds the shown order for 5 s, then re-ranks", () => {
    const a = holdOrder(null, [1, 2, 3], none, 0);
    const held = holdOrder(a, [2, 1, 3], none, 2000);
    expect(held.ids).toEqual([1, 2, 3]);
    expect(holdOrder(held, [2, 1, 3], none, 2500)).toBe(held); // stable identity while nothing changes
    expect(holdOrder(held, [2, 1, 3], none, 5000).ids).toEqual([2, 1, 3]);
  });

  it("re-ranks at once after a quiet spell", () => {
    const a = holdOrder(null, [1, 2], none, 0);
    expect(holdOrder(a, [2, 1], none, 60_000).ids).toEqual([2, 1]);
  });

  it("drops residents who leave and appends new ones while held", () => {
    const a = holdOrder(null, [1, 2, 3], none, 0);
    expect(holdOrder(a, [4, 3, 1], none, 1000).ids).toEqual([1, 3, 4]);
  });

  it("a CRITICAL resident moving up interrupts the hold", () => {
    const a = holdOrder(null, [1, 2, 3], none, 0);
    expect(holdOrder(a, [3, 1, 2], new Set([3]), 1000).ids).toEqual([3, 1, 2]);
    expect(holdOrder(a, [9, 1, 2, 3], new Set([9]), 1000).ids).toEqual([9, 1, 2, 3]);
  });

  it("arranges rows and words the summary", () => {
    expect(arrange([{ id: 1 }, { id: 2 }], [2, 1, 5]).map((r) => r.id)).toEqual([2, 1]);
    const parts = summaryParts({ tiers: { CRITICAL: 0, HIGH: 1, WATCH: 2, STABLE: 117 }, overdue_checks: 9, devices_offline: 1 });
    expect(parts.map((p) => `${p.n} ${p.text}`)).toEqual([
      "1 to check soon", "2 to keep an eye on", "117 resting as usual", "1 device task", "9 rounds overdue",
    ]);
    const fall = summaryParts({ tiers: { CRITICAL: 1, HIGH: 0, WATCH: 0, STABLE: 119 }, overdue_checks: 0, devices_offline: 0 });
    expect(fall.map((p) => `${p.n} ${p.text}`)).toEqual(["1 to go to now", "119 resting as usual"]);
  });
});
