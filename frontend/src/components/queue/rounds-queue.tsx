"use client";

import { Loader2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";

import { FloorGrid } from "@/components/queue/floor-grid";
import { NextCard } from "@/components/queue/next-card";
import { WatchList } from "@/components/queue/watch-list";
import { useClock } from "@/hooks/use-clock";
import { useNow } from "@/hooks/use-now";
import { minutesSince } from "@/lib/format";
import {
  arrange,
  type HeldOrder,
  holdOrder,
  queueRows,
  type QueueRow,
  reuseRows,
  splitFlagged,
  summaryParts,
} from "@/lib/queue";
import { useLiveStore } from "@/store/live";

export function RoundsQueue() {
  const { queue, roster, status } = useLiveStore(useShallow((s) => ({ queue: s.queue, roster: s.roster, status: s.status })));
  const { now } = useClock();
  const wall = useNow();
  const [rowCache] = useState(() => new Map<number, { sig: string; row: QueueRow }>());
  const rows = useMemo(() => reuseRows(rowCache, queueRows(queue, roster)), [queue, roster, rowCache]);
  const { flagged, stable } = useMemo(() => splitFlagged(rows), [rows]);

  // Cards hold their place for 5 s before re-ranking; a CRITICAL resident moving up interrupts at once.
  const [held, setHeld] = useState<HeldOrder | null>(null);
  const ranked = flagged.map((r) => r.id);
  const critical = new Set(flagged.filter((r) => r.tier === "CRITICAL").map((r) => r.id));
  const next = holdOrder(held, ranked, critical, wall ?? 0);
  if (next !== held) setHeld(next); // derived state, updated during render
  const shown = arrange(flagged, next.ids);
  const [first, ...watch] = shown;
  const tasks = rows.filter((r) => r.task || r.device);

  // Ticks every minute without waiting for the next queue message.
  const minuteNow = now === null ? null : Math.floor(now / 60000) * 60000;
  const minutes = (r: QueueRow) =>
    minuteNow === null ? r.check_min : (minutesSince(r.last_check, minuteNow) ?? r.check_min);

  return (
    <main className="mx-auto w-full max-w-[80rem] flex-1 px-4 pt-6 pb-16 sm:px-6">
      <h1 className="mb-2.5 text-[13px] tracking-[0.08em] text-soft uppercase">Rounds queue · who to check next</h1>
      {queue && (
        <p className="mb-[18px] flex flex-wrap gap-x-5 gap-y-2 text-muted-foreground" aria-label="Facility summary">
          {summaryParts(queue.kpis).map((p) => (
            <span key={p.text}>
              <b className="text-foreground">{p.n}</b> {p.text}
            </span>
          ))}
        </p>
      )}

      {!queue ? (
        <div className="flex h-64 items-center justify-center gap-3 rounded-[18px] border border-dashed border-line text-lg text-muted-foreground">
          <Loader2 className="size-6 animate-spin" aria-hidden />
          {status === "open" ? "Waiting for the first scored queue…" : "Connecting to the live feed…"}
        </div>
      ) : (
        <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
          <section aria-labelledby="next-heading" className="min-w-0">
            <h2 id="next-heading" className="vg-label mb-2.5">
              Check next
            </h2>
            {first ? (
              <NextCard key={first.id} row={first} minutesSinceCheck={minutes(first)} />
            ) : (
              <p className="vg-card p-6 text-xl">Everyone is resting as usual. Keep to the scheduled rounds.</p>
            )}
            <WatchList watch={watch} tasks={tasks} stable={stable} minutes={minutes} />
          </section>
          <section aria-labelledby="floor-heading" className="min-w-0 lg:sticky lg:top-4">
            <h2 id="floor-heading" className="vg-label mb-2.5">
              Floor
            </h2>
            <FloorGrid rows={rows} />
          </section>
        </div>
      )}
    </main>
  );
}
