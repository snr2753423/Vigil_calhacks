"use client";

import { WatchIcon } from "lucide-react";
import Link from "next/link";
import { memo } from "react";
import { cn } from "cn";

import { floorRooms, type QueueRow, UNITS } from "@/lib/queue";
import { TIER_ORDER, TIERS } from "@/lib/tiers";

/** All 120 rooms by unit, colored by tier with the tier icon on flagged rooms; CRITICAL pulses. */
export const FloorGrid = memo(function FloorGrid({ rows }: { rows: QueueRow[] }) {
  const rooms = floorRooms(rows);
  return (
    <section aria-labelledby="floor-heading" className="space-y-4 rounded-2xl bg-card/60 p-4 ring-1 ring-white/10">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="floor-heading" className="text-lg font-semibold">
          Floor
        </h2>
        <ul className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground" aria-label="Legend">
          {TIER_ORDER.map((t) => {
            const Icon = TIERS[t].icon;
            return (
              <li key={t} className="flex items-center gap-1">
                <span className={cn("flex size-5 items-center justify-center rounded", TIERS[t].solid)}>
                  <Icon className="size-3.5" aria-hidden />
                </span>
                {TIERS[t].label}
              </li>
            );
          })}
        </ul>
      </div>
      {UNITS.map((u) => (
        <div key={u.unit}>
          <h3 className="mb-1.5 text-sm font-medium text-muted-foreground">{u.label}</h3>
          <ul className="grid grid-cols-8 gap-1.5">
            {rooms[u.unit].map(({ room, row }) => (
              <li key={room}>
                <Room room={room} row={row} />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
});

function Room({ room, row }: { room: string; row: QueueRow | undefined }) {
  if (!row) {
    return (
      <span className="flex aspect-square items-center justify-center rounded-md bg-white/[0.03] text-xs text-muted-foreground/60">
        {room}
      </span>
    );
  }
  const t = TIERS[row.tier];
  const Icon = t.icon;
  const flagged = row.tier !== "STABLE";
  const label = `Room ${room}, ${row.resident?.name ?? `resident ${row.id}`}: ${t.label}, score ${row.score}${
    row.device ? `, ${row.device}` : ""
  }`;
  return (
    <Link
      href={`/resident/${row.id}`}
      aria-label={label}
      title={label}
      className={cn(
        "relative flex aspect-square min-h-9 flex-col items-center justify-center rounded-md text-xs font-semibold tabular-nums transition-colors",
        t.solid,
        row.tier === "CRITICAL" && "vigil-pulse",
        !flagged && "font-normal",
      )}
    >
      {flagged && <Icon className="size-4" aria-hidden />}
      <span className={flagged ? "text-[0.65rem] leading-none" : undefined}>{room}</span>
      {row.device && (
        <WatchIcon className="absolute right-0.5 top-0.5 size-3 text-sky-200" aria-hidden />
      )}
    </Link>
  );
}
