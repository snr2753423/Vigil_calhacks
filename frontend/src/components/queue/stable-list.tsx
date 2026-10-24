"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { TierChip } from "@/components/tier-chip";
import { formatMinutes } from "@/lib/format";
import type { QueueRow } from "@/lib/queue";

const COLLAPSED = 6;

/** Residents scoring STABLE: one line each, routine rounds. */
export function StableList({ rows, minutes }: { rows: QueueRow[]; minutes: (row: QueueRow) => number | null }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, COLLAPSED);
  return (
    <section aria-labelledby="stable-heading" className="rounded-2xl bg-card/60 ring-1 ring-white/10">
      <h2 id="stable-heading" className="flex items-center gap-2 px-4 pt-3 text-lg font-semibold">
        <TierChip tier="STABLE" size="sm" /> {rows.length} residents · routine rounds
      </h2>
      <ul className="divide-y divide-white/5 px-2 py-2">
        {shown.map((r) => {
          const m = minutes(r);
          return (
            <li key={r.id}>
              <Link
                href={`/resident/${r.id}`}
                className="flex min-h-12 items-center gap-3 rounded-lg px-2 py-2 hover:bg-white/5"
              >
                <span className="w-9 font-mono text-sm text-muted-foreground tabular-nums">#{r.rank}</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-base font-medium">{r.resident?.name ?? `Resident ${r.id}`}</span>
                  <span className="block truncate text-sm text-muted-foreground">
                    Room {r.resident?.room ?? "—"}
                    {r.factors[0] ? ` · ${r.factors[0].text}` : ""}
                  </span>
                </span>
                <span className="hidden text-sm text-muted-foreground sm:inline">
                  {m === null ? "no check" : `checked ${formatMinutes(m)} ago`}
                </span>
                <span className="w-8 text-right font-mono text-base tabular-nums text-muted-foreground">{r.score}</span>
                <ChevronRight className="size-5 text-muted-foreground" aria-hidden />
              </Link>
            </li>
          );
        })}
      </ul>
      {rows.length > COLLAPSED && (
        <button
          type="button"
          onClick={() => setAll(!all)}
          aria-expanded={all}
          className="flex h-12 w-full items-center justify-center gap-1.5 border-t border-white/5 text-base text-muted-foreground hover:bg-white/5"
        >
          <ChevronDown className={`size-5 transition-transform ${all ? "rotate-180" : ""}`} aria-hidden />
          {all ? "Show fewer" : `Show all ${rows.length}`}
        </button>
      )}
    </section>
  );
}
