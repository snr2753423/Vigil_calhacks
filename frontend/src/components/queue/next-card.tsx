"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Avatar } from "@/components/avatar";
import { CodeStatusBadge } from "@/components/code-status-badge";
import { MarkCheckedForm } from "@/components/mark-checked-form";
import { getJSON } from "@/lib/api";
import { facilityClock12, firstName, plainMinutes } from "@/lib/format";
import { quietNote, type QueueRow } from "@/lib/queue";
import { TIERS } from "@/lib/tiers";
import type { Assessment } from "@/lib/types";
import { cn } from "cn";

type Props = { row: QueueRow; minutesSinceCheck: number | null };

const EYEBROW: Record<QueueRow["tier"], string> = {
  CRITICAL: "go now",
  HIGH: "please check within 15 minutes",
  WATCH: "check first on your next round",
  STABLE: "resting as usual",
};


/** The one resident to check next: who, why in plain words, what to look for, and the two actions. */
export function NextCard({ row, minutesSinceCheck }: Props) {
  const r = row.resident;
  const name = r?.name ?? `Resident ${row.id}`;
  const first = firstName(name);
  const t = TIERS[row.tier];
  const Icon = t.icon;
  const her = r?.sex === "M" ? "his" : "her";
  return (
    <article
      className={cn("vg-card border-l-[6px] p-5 sm:p-6", t.edge)}
      aria-labelledby={`next-${row.id}`}
      data-testid="check-next"
    >
      <p className={cn("flex items-center gap-2.5 font-bold", t.text)}>
        <Icon className="size-[18px]" strokeWidth={2.4} aria-hidden />
        {t.label} · {EYEBROW[row.tier]}
      </p>
      <div className="mt-3.5 mb-1.5 flex flex-wrap items-center gap-4">
        <Avatar name={name} />
        <div className="min-w-0">
          <h3 id={`next-${row.id}`} className="text-2xl leading-tight font-bold sm:text-[28px]">
            {name}
          </h3>
          <p className="text-muted-foreground">
            Room {r?.room ?? "—"} · {r?.unit ?? ""}
            {r ? ` · ${r.age}` : ""}
            {r?.is_live && " · live sensors"}
          </p>
        </div>
        {r && <CodeStatusBadge status={r.code_status} className="ml-auto self-start" />}
      </div>

      <p className="mt-3.5 mb-2.5 text-xl leading-snug sm:text-[21px]">{row.plain_reason ?? row.factors[0]?.text}</p>

      <ul className="flex flex-wrap gap-x-[18px] gap-y-1.5 text-[15px] text-muted-foreground" aria-label="The numbers behind it">
        {row.factors.map((f) => (
          <li key={f.text}>{f.text}</li>
        ))}
        <li>
          {minutesSinceCheck === null ? (
            "No check logged today"
          ) : (
            <>
              Last checked <b className="font-bold text-foreground">{plainMinutes(minutesSinceCheck)}</b> ago
            </>
          )}
        </li>
      </ul>

      {quietNote(row) && <p className="mt-2 text-[15px] text-muted-foreground">{quietNote(row)}</p>}

      <WorthChecking residentId={row.id} ai={row.ai} her={her} />

      <div className="mt-[18px] flex flex-wrap items-center gap-3">
        <MarkCheckedForm residentId={row.id} name={name} compact />
        <Link href={`/resident/${row.id}`} className="vg-btn-secondary">
          Open {first}&apos;s page
        </Link>
      </div>
    </article>
  );
}

/** "Worth checking" from the resident's newest Snowflake Cortex card: the one the queue's headline names
 *  (`ai.at`), read once from the ai_insights cache rather than polling the full resident detail. */
function WorthChecking({ residentId, ai, her }: { residentId: number; ai: QueueRow["ai"]; her: string }) {
  const key = ai ? `${residentId}|${ai.at}` : null;
  const [card, setCard] = useState<{ key: string; a: Assessment | null } | null>(null);
  useEffect(() => {
    if (!ai || !key) return;
    const ctrl = new AbortController();
    const at = Date.parse(ai.at);
    const q = new URLSearchParams({ kind: "assessment", from: new Date(at - 60_000).toISOString(), to: new Date(at + 1000).toISOString() });
    getJSON<{ items: Assessment[] }>(`/api/insights/${residentId}?${q}`, ctrl.signal)
      .then((r) => setCard({ key, a: r.items.at(-1) ?? null }))
      .catch(() => !ctrl.signal.aborted && setCard({ key, a: null }));
    return () => ctrl.abort();
  }, [residentId, ai, key]);

  const a = card?.key === key ? card.a : null;
  if (!ai) {
    return (
      <p className="mt-[18px] rounded-xl bg-surface-2 px-4 py-3.5 text-muted-foreground">
        Vigil&apos;s suggestions for {her === "his" ? "him" : "her"} aren&apos;t ready yet; the checklist is on {her} page.
      </p>
    );
  }
  if (!a) {
    return <div className="mt-[18px] h-24 rounded-xl bg-surface-2" aria-hidden />;
  }
  return (
    <div className="mt-[18px] rounded-xl bg-surface-2 px-4 py-3.5">
      {a.recommended_checks.length > 0 && (
        <p className="mb-1.5">
          <b>Worth checking:</b> {checksSentence(a.recommended_checks.slice(0, 3))}
        </p>
      )}
      {a.care_plan_citations[0] && (
        <p className="mb-1.5">
          {her === "his" ? "His" : "Her"} care plan: “{a.care_plan_citations[0].quote}”
        </p>
      )}
      <p className="mt-1.5 text-sm text-soft">{sourceLine(a, ai.at, her)}</p>
    </div>
  );
}

function checksSentence(checks: string[]): string {
  const clean = checks.map((c) => c.trim().replace(/[.;]+$/, ""));
  // "Check temperature" → "check temperature", but "SpO2 …" / "NEWS2 …" keep their capitals.
  const lower = clean.map((c, i) => (i === 0 || !/^.[a-z]/.test(c) ? c : c.charAt(0).toLowerCase() + c.slice(1)));
  return lower.join("; ") + ".";
}

export function sourceLine(a: Assessment, asOf: string | undefined, her: string): string {
  const at = facilityClock12(Date.parse(a.as_of ?? asOf ?? new Date().toISOString()));
  return a.source === "cortex"
    ? `Suggested by Vigil (AI, from ${her} vitals and care plan) · ${at} · decision support, not a diagnosis`
    : `Suggested by Vigil from ${her} scores (the AI card is not ready yet) · ${at} · decision support, not a diagnosis`;
}
