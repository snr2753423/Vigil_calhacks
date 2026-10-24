"use client";

import { ChevronRight, WatchIcon } from "lucide-react";
import Link from "next/link";
import { memo, useState } from "react";

import { Avatar } from "@/components/avatar";
import { TierChip } from "@/components/tier-chip";
import { plainMinutes } from "@/lib/format";
import { quietNote, type QueueRow } from "@/lib/queue";

type Props = {
  watch: QueueRow[];
  tasks: QueueRow[];
  stable: QueueRow[];
  minutes: (row: QueueRow) => number | null;
};

/** "Keep an eye on": the other flagged residents, device tasks in plain words, and everyone resting as usual. */
export function WatchList({ watch, tasks, stable, minutes }: Props) {
  const [all, setAll] = useState(false);
  return (
    <section className="vg-card mt-[18px] overflow-hidden" aria-labelledby="watch-heading">
      <h2 id="watch-heading" className="vg-label px-[18px] pt-4 pb-1">
        Keep an eye on
      </h2>
      {watch.length === 0 && <p className="px-[18px] py-3 text-muted-foreground">No one else needs a closer look right now.</p>}
      <ul>
        {watch.map((r, i) => (
          <li key={r.id} className={i > 0 ? "border-t border-line" : undefined}>
            <Row row={r} minutes={minutes(r)} />
          </li>
        ))}
      </ul>
      {tasks.map((r) => (
        <p key={`task-${r.id}`} className="flex items-start gap-3 border-t border-line px-[18px] py-3.5 text-muted-foreground">
          <WatchIcon className="mt-0.5 size-5 shrink-0" aria-hidden />
          <span>
            <b className="text-foreground">Task:</b> {r.task ?? `${r.resident?.name ?? `Resident ${r.id}`}: ${r.device}`}
          </span>
        </p>
      ))}
      <div className="border-t border-line">
        <button
          type="button"
          onClick={() => setAll(!all)}
          aria-expanded={all}
          className="flex min-h-[52px] w-full items-center justify-between px-[18px] text-left text-muted-foreground hover:bg-surface-2"
        >
          <span>
            {stable.length} resident{stable.length === 1 ? "" : "s"} resting as usual
          </span>
          <span className="flex items-center gap-1">
            {all ? "Hide" : "Show all"} <ChevronRight className={`size-5 transition-transform ${all ? "rotate-90" : ""}`} aria-hidden />
          </span>
        </button>
        {all && (
          <ul className="border-t border-line">
            {stable.map((r, i) => (
              <li key={r.id} className={i > 0 ? "border-t border-line" : undefined}>
                <Row row={r} minutes={minutes(r)} quiet />
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

const Row = memo(function Row({ row, minutes, quiet = false }: { row: QueueRow; minutes: number | null; quiet?: boolean }) {
  const name = row.resident?.name ?? `Resident ${row.id}`;
  return (
    <Link
      href={`/resident/${row.id}`}
      className="flex flex-wrap items-center gap-x-3.5 gap-y-2 px-[18px] py-3.5 hover:bg-surface-2"
    >
      <Avatar name={name} size="sm" />
      <span className="min-w-0 flex-1 basis-[calc(100%-60px)] sm:basis-0">
        <span className="block text-lg font-bold">
          {name} <span className="font-normal text-muted-foreground">· Room {row.resident?.room ?? "—"}</span>
        </span>
        <span className="block text-muted-foreground">
          {row.plain_reason ?? (row.tier === "STABLE" ? "Resting as usual." : row.factors[0]?.text)}
        </span>
        {!quiet && quietNote(row) && <span className="block text-sm text-soft">{quietNote(row)}</span>}
      </span>
      {!quiet && <TierChip tier={row.tier} size="sm" />}
      <span className="whitespace-nowrap text-sm text-soft">
        {minutes === null ? "no check logged" : `checked ${plainMinutes(minutes)} ago`}
      </span>
    </Link>
  );
});
