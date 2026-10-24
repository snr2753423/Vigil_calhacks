"use client";

import { ChevronDown, RefreshCw, Sparkles } from "lucide-react";
import { useState } from "react";

import { facilityHHMM } from "@/lib/format";
import type { Assessment } from "@/lib/types";

type Props = { a: Assessment; asOf: string; onRefresh: () => void; refreshing: boolean };

/** The AI assessment card. Until Snowflake Cortex is wired (Phase 6) it shows the deterministic template. */
export function AssessmentCard({ a, asOf, onRefresh, refreshing }: Props) {
  const [done, setDone] = useState<Set<number>>(new Set());
  const [showCites, setShowCites] = useState(false);
  const template = a.source === "template";
  return (
    <section aria-labelledby="ai-heading" className="space-y-3 rounded-2xl bg-card p-4 ring-1 ring-indigo-300/25">
      <div className="flex items-start justify-between gap-3">
        <h2 id="ai-heading" className="flex items-center gap-2 text-sm font-medium uppercase tracking-wide text-indigo-200">
          <Sparkles className="size-4" aria-hidden /> {template ? "Summary (template)" : "AI assessment"}
        </h2>
        <button
          type="button"
          onClick={onRefresh}
          className="flex h-11 items-center gap-1.5 rounded-lg px-3 text-base text-muted-foreground ring-1 ring-white/10 hover:bg-white/5"
        >
          <RefreshCw className={`size-4 ${refreshing ? "animate-spin" : ""}`} aria-hidden /> Refresh
        </button>
      </div>
      <p className="text-xl font-semibold leading-snug">{a.headline}</p>
      <p className="text-base leading-relaxed">{a.assessment}</p>

      <div>
        <h3 className="mb-1.5 text-base font-semibold">Consider checking</h3>
        <ul className="space-y-1.5">
          {a.recommended_checks.map((c, i) => (
            <li key={c}>
              <label className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-2 hover:bg-white/5">
                <input
                  type="checkbox"
                  className="size-5 accent-teal-400"
                  checked={done.has(i)}
                  onChange={() => {
                    const next = new Set(done);
                    if (next.has(i)) next.delete(i);
                    else next.add(i);
                    setDone(next);
                  }}
                />
                <span className={done.has(i) ? "text-muted-foreground line-through" : ""}>{c}</span>
              </label>
            </li>
          ))}
        </ul>
      </div>

      {a.escalation && (
        <div className="rounded-lg bg-white/[0.04] p-3">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Escalation</h3>
          <p className="text-base">{a.escalation}</p>
        </div>
      )}

      <div>
        <button
          type="button"
          onClick={() => setShowCites(!showCites)}
          aria-expanded={showCites}
          className="flex h-11 items-center gap-1.5 text-base text-muted-foreground hover:text-foreground"
        >
          <ChevronDown className={`size-5 transition-transform ${showCites ? "rotate-180" : ""}`} aria-hidden />
          Care-plan citations ({a.care_plan_citations.length})
        </button>
        {showCites &&
          (a.care_plan_citations.length ? (
            <ul className="space-y-2 pl-2">
              {a.care_plan_citations.map((c) => (
                <li key={c.quote} className="border-l-2 border-indigo-300/40 pl-3">
                  <p className="text-sm text-muted-foreground">{c.section}</p>
                  <p className="text-base">“{c.quote}”</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="pl-2 text-base text-muted-foreground">
              Care-plan citations come from Snowflake Cortex Search once it is connected.
            </p>
          ))}
      </div>

      <p className="text-sm text-muted-foreground">
        {template ? "Deterministic template from Vigil's scoring factors" : `Snowflake Cortex · ${a.model}`} ·{" "}
        {facilityHHMM(Date.parse(asOf))} · confidence {a.confidence}
        <br />
        {a.caveats}
      </p>
    </section>
  );
}
