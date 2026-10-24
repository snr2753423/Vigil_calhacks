"use client";

import Link from "next/link";
import { memo } from "react";
import { cn } from "cn";

import { floorRooms, type QueueRow, ROOMS_PER_UNIT, UNITS } from "@/lib/queue";
import { TIERS } from "@/lib/tiers";

const PER_ROW = 10; // a hallway: 10 rooms on each side

/** The floor as it is walked: each unit's rooms on both sides of its hallways, the nurses' station between units,
 *  rooms tinted by tier (with the tier word in the label), a dot for a device task. */
export const FloorGrid = memo(function FloorGrid({ rows }: { rows: QueueRow[] }) {
  const rooms = floorRooms(rows);
  return (
    <div className="vg-card p-[18px]">
      {UNITS.map((u, ui) => {
        const cells = rooms[u.unit];
        const halls = [];
        for (let i = 0; i < ROOMS_PER_UNIT; i += 2 * PER_ROW) halls.push([cells.slice(i, i + PER_ROW), cells.slice(i + PER_ROW, i + 2 * PER_ROW)]);
        return (
          <div key={u.unit}>
            {ui > 0 && (
              <div className="mt-1 mb-4 rounded-[10px] border border-dashed border-line p-2 text-center text-sm text-muted-foreground">
                Nurses&apos; station
              </div>
            )}
            <section className="mb-[18px]" aria-label={`${u.label}, rooms ${u.first}–${u.first + ROOMS_PER_UNIT - 1}`}>
              <h3 className="mb-1.5 text-sm text-muted-foreground">
                {u.label} · rooms {u.first}–{u.first + ROOMS_PER_UNIT - 1}
              </h3>
              <div className="space-y-2">
                {halls.map(([a, b], hi) => (
                  <div key={hi}>
                    <RoomRow cells={a} />
                    <div
                      className="my-0.5 flex h-[18px] items-center justify-center rounded text-[11px] tracking-[0.1em] text-soft uppercase"
                      style={{ background: "repeating-linear-gradient(90deg, transparent 0 10px, var(--line) 10px 11px)" }}
                      aria-hidden
                    >
                      hallway
                    </div>
                    <RoomRow cells={b} />
                  </div>
                ))}
              </div>
            </section>
          </div>
        );
      })}
      <ul className="mt-1 flex flex-wrap items-center gap-2 text-sm" aria-label="Legend">
        {(["CRITICAL", "HIGH", "WATCH"] as const).map((t) => (
          <li key={t} className={cn("rounded-full px-2.5 py-0.5 font-bold", TIERS[t].pill)}>
            {TIERS[t].label}
          </li>
        ))}
        <li className={cn("rounded-full px-2.5 py-0.5 font-bold", TIERS.STABLE.pill)}>Resting</li>
        <li className="flex items-center gap-1.5 text-muted-foreground">
          <span className="size-2 rounded-full bg-muted-foreground" aria-hidden /> Device task
        </li>
      </ul>
    </div>
  );
});

function RoomRow({ cells }: { cells: { room: string; row: QueueRow | undefined }[] }) {
  return (
    <ul className="grid grid-cols-5 gap-1 sm:grid-cols-10">
      {cells.map(({ room, row }) => (
        <li key={room}>
          <Room room={room} row={row} />
        </li>
      ))}
    </ul>
  );
}

function Room({ room, row }: { room: string; row: QueueRow | undefined }) {
  const base = "relative grid h-[34px] place-items-center rounded-md text-xs";
  if (!row) {
    return <span className={cn(base, "bg-surface-2 text-soft opacity-60")}>{room}</span>;
  }
  const t = TIERS[row.tier];
  const name = row.resident?.name ?? `resident ${row.id}`;
  const label = `Room ${room}, ${name}: ${row.tier === "STABLE" ? "resting as usual" : `${t.label}, ${t.action.toLowerCase()}`}${
    row.task || row.device ? ", device task" : ""
  }`;
  return (
    <Link
      href={`/resident/${row.id}`}
      aria-label={label}
      title={label}
      className={cn(base, t.room, row.tier === "CRITICAL" && "vigil-pulse", "hover:brightness-110")}
    >
      {room}
      {(row.task || row.device) && <span className="absolute top-1 right-1 size-[7px] rounded-full bg-muted-foreground" aria-hidden />}
    </Link>
  );
}
