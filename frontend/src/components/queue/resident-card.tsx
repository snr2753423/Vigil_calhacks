"use client";

import { BedDouble, ChevronRight, Clock, Footprints, Radio, Sparkles, VolumeX, WatchIcon } from "lucide-react";
import Link from "next/link";
import { memo } from "react";
import { cn } from "cn";

import { CodeStatusBadge } from "@/components/code-status-badge";
import { FactorChip } from "@/components/factor-chip";
import { MarkCheckedForm } from "@/components/mark-checked-form";
import { Sparkline } from "@/components/queue/sparkline";
import { TierChip } from "@/components/tier-chip";
import { formatMinutes } from "@/lib/format";
import type { QueueRow } from "@/lib/queue";
import { TIERS } from "@/lib/tiers";

type Props = { row: QueueRow; minutesSinceCheck: number | null };

/** A flagged resident in the Rounds Queue: who, how urgent, the 3 reasons, and quick actions. */
export const ResidentCard = memo(function ResidentCard({ row, minutesSinceCheck }: Props) {
  const r = row.resident;
  const name = r?.name ?? `Resident ${row.id}`;
  const t = TIERS[row.tier];
  return (
    <article
      data-flip={row.id}
      className={cn(
        "relative overflow-hidden rounded-2xl bg-card p-4 pl-5 ring-1 ring-white/10",
        row.tier === "CRITICAL" && "ring-2 ring-red-400/70",
      )}
      aria-labelledby={`card-${row.id}`}
    >
      <span className={cn("absolute inset-y-0 left-0 w-1.5", t.accent)} aria-hidden />
      <header className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <span className="mt-0.5 font-mono text-lg text-muted-foreground tabular-nums" aria-label={`Rank ${row.rank}`}>
          #{row.rank}
        </span>
        <div className="min-w-0 flex-1">
          <h3 id={`card-${row.id}`} className="text-xl font-semibold leading-tight">
            {name}
          </h3>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-base text-muted-foreground">
            <span>
              Room {r?.room ?? "—"} · {r?.unit ?? ""}
            </span>
            {r && <CodeStatusBadge status={r.code_status} />}
            {r?.is_live && (
              <span className="inline-flex h-7 items-center gap-1 rounded-lg bg-teal-500/15 px-2 text-xs font-semibold uppercase text-teal-100">
                <Radio className="size-3.5" aria-hidden /> Live sensors
              </span>
            )}
          </p>
        </div>
        <TierChip tier={row.tier} score={row.score} size="md" />
      </header>

      {row.rule && (
        <p
          className={cn(
            "mt-3 rounded-lg px-3 py-2 text-base font-medium",
            row.rule.kind === "device" ? "bg-slate-500/20" : "bg-red-500/15 text-red-50",
          )}
        >
          {row.rule.text}
          {row.rule.ack && <span className="ml-2 text-sm font-normal opacity-80">(acknowledged)</span>}
        </p>
      )}

      <ul className="mt-3 flex flex-wrap gap-2" aria-label="Top reasons">
        {row.factors
          .filter((f) => !(row.rule && f.kind === "rule" && f.text === row.rule.text))
          .map((f) => (
            <li key={f.text}>
              <FactorChip text={f.text} kind={f.kind} />
            </li>
          ))}
        {row.device && !row.factors.some((f) => f.kind === "device") && (
          <li>
            <FactorChip text={row.device} kind="device" />
          </li>
        )}
      </ul>

      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-base">
        <div className="flex items-center gap-3">
          <Sparkline values={row.spark ?? []} color={t.hex} label={`Heart rate, last 2 hours, now ${row.hr ?? "unknown"}`} />
          <Vitals row={row} />
        </div>
        <span className="flex items-center gap-1.5 text-muted-foreground">
          <Clock className="size-4" aria-hidden />
          {minutesSinceCheck === null ? "No check today" : `Checked ${formatMinutes(minutesSinceCheck)} ago`}
        </span>
        <BedState inBed={row.in_bed} worn={row.worn} />
        {row.suppressed && (
          <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <VolumeX className="size-4" aria-hidden /> Quiet after check
          </span>
        )}
      </div>

      <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
        <Sparkles className="size-4 shrink-0" aria-hidden />
        <span>AI summary pending · Snowflake Cortex</span>
      </p>

      <footer className="mt-3 flex flex-wrap items-start gap-2">
        <Link
          href={`/resident/${row.id}`}
          className="flex h-12 items-center gap-1 rounded-xl bg-white/10 px-4 text-base font-semibold hover:bg-white/15"
        >
          Open <ChevronRight className="size-5" aria-hidden />
        </Link>
        <MarkCheckedForm residentId={row.id} name={name} compact />
      </footer>
    </article>
  );
});

function Vitals({ row }: { row: QueueRow }) {
  return (
    <dl className="flex gap-3 font-mono tabular-nums">
      <div>
        <dt className="text-xs text-muted-foreground">HR</dt>
        <dd className="text-lg">{row.hr ?? "—"}</dd>
      </div>
      <div>
        <dt className="text-xs text-muted-foreground">RR</dt>
        <dd className="text-lg">{row.rr ?? "—"}</dd>
      </div>
      <div>
        <dt className="text-xs text-muted-foreground">SpO₂</dt>
        <dd className="text-lg">{row.spo2 ?? "—"}</dd>
      </div>
    </dl>
  );
}

function BedState({ inBed, worn }: { inBed: boolean | null; worn: boolean | null }) {
  return (
    <span className="flex items-center gap-3 text-muted-foreground">
      {inBed === null ? null : inBed ? (
        <span className="flex items-center gap-1.5">
          <BedDouble className="size-4" aria-hidden /> In bed
        </span>
      ) : (
        <span className="flex items-center gap-1.5 text-amber-100">
          <Footprints className="size-4" aria-hidden /> Out of bed
        </span>
      )}
      {worn === false && (
        <span className="flex items-center gap-1.5">
          <WatchIcon className="size-4" aria-hidden /> Band off
        </span>
      )}
    </span>
  );
}
