"use client";

import { Loader2 } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";

import { FloorGrid } from "@/components/queue/floor-grid";
import { KpiStrip } from "@/components/queue/kpi-strip";
import { ResidentCard } from "@/components/queue/resident-card";
import { StableList } from "@/components/queue/stable-list";
import { useFlip } from "@/hooks/use-flip";
import { useNow } from "@/hooks/use-now";
import { facilityHHMM, minutesSince } from "@/lib/format";
import { queueRows, type QueueRow, reuseRows, splitFlagged } from "@/lib/queue";
import { useLiveStore } from "@/store/live";

export function RoundsQueue() {
  const { queue, roster, status, sim } = useLiveStore(
    useShallow((s) => ({ queue: s.queue, roster: s.roster, status: s.status, sim: s.sim })),
  );
  const now = useNow();
  const [rowCache] = useState(() => new Map<number, { sig: string; row: QueueRow }>());
  const rows = useMemo(() => reuseRows(rowCache, queueRows(queue, roster)), [queue, roster, rowCache]);
  const { flagged, stable } = useMemo(() => splitFlagged(rows), [rows]);
  const listRef = useRef<HTMLOListElement>(null);
  const order = flagged.map((r) => r.id).join(",");
  useFlip(listRef, order);

  // Ticks every minute without waiting for the next queue message.
  const minuteNow = now === null ? null : Math.floor(now / 60000) * 60000;
  const minutes = (r: QueueRow) =>
    minuteNow === null ? r.check_min : (minutesSince(r.last_check, minuteNow) ?? r.check_min);

  return (
    <main className="mx-auto w-full max-w-[96rem] flex-1 space-y-4 px-3 py-4 sm:px-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Rounds Queue</h1>
          <p className="text-base text-muted-foreground">Who to check next, and why.</p>
        </div>
        {queue && (
          <p className="text-sm text-muted-foreground" aria-live="off">
            Scored {facilityHHMM(Date.parse(queue.as_of), sim?.display_clock_offset_s)} · {queue.eval_ms} ms
            {sim?.paused && " · paused"}
          </p>
        )}
      </div>

      <KpiStrip kpis={queue?.kpis ?? null} />

      {!queue ? (
        <div className="flex h-64 items-center justify-center gap-3 rounded-2xl border border-dashed border-white/15 text-lg text-muted-foreground">
          <Loader2 className="size-6 animate-spin" aria-hidden />
          {status === "open" ? "Waiting for the first scored queue…" : "Connecting to the live feed…"}
        </div>
      ) : (
        <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 lg:grid-cols-[minmax(0,1fr)_25rem] xl:grid-cols-[minmax(0,1fr)_28rem]">
          <section aria-labelledby="flagged-heading" className="min-w-0 space-y-3">
            <h2 id="flagged-heading" className="sr-only">
              Residents to check
            </h2>
            {flagged.length === 0 ? (
              <p className="rounded-2xl bg-card p-6 text-lg text-muted-foreground ring-1 ring-white/10">
                Everyone is stable right now. Keep to the scheduled rounds.
              </p>
            ) : (
              <ol ref={listRef} className="relative space-y-3">
                {flagged.map((r) => (
                  <li key={r.id} className="animate-in fade-in duration-500">
                    <ResidentCard row={r} minutesSinceCheck={minutes(r)} />
                  </li>
                ))}
              </ol>
            )}
            <StableList rows={stable} minutes={minutes} />
          </section>
          <aside className="min-w-0 lg:sticky lg:top-20">
            <FloorGrid rows={rows} />
          </aside>
        </div>
      )}
    </main>
  );
}
