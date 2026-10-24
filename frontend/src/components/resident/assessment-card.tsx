"use client";

import { ChevronDown, Info, Loader2, RefreshCw, Sparkles } from "lucide-react";
import { useState } from "react";

import { facilityHHMM } from "@/lib/format";
import type { Assessment } from "@/lib/types";

/** `replay`: the card was prepared ahead for this replay moment; Refresh (a live Cortex call) is off. */
type Props = { a: Assessment; asOf: string; onRefresh: () => void; refreshing: boolean; replay?: boolean };

/** The AI assessment card: a Snowflake Cortex assessment with care-plan citations, or the deterministic
 *  template when none is fresh or Snowflake is slow/down (same shape, never blank). */
export function AssessmentCard(props: Props) {
  const { a } = props;
  // Remount the checklist when a new assessment arrives so old ticks don't carry over to new checks.
  return <AssessmentBody key={`${a.source}|${a.as_of ?? ""}|${a.headline}`} {...props} />;
}

function AssessmentBody({ a, asOf, onRefresh, refreshing, replay = false }: Props) {
  const [done, setDone] = useState<Set<number>>(new Set());
  const [showCites, setShowCites] = useState(false);
  const template = a.source === "template";
  const updating = refreshing || a.pending === true;
  const at = facilityHHMM(Date.parse(a.as_of ?? asOf));
  return (
    <section aria-labelledby="ai-heading" className="space-y-3 rounded-2xl bg-card p-4 ring-1 ring-indigo-300/25">
      <div className="flex items-start justify-between gap-3">
        <h2 id="ai-heading" className="flex items-center gap-2 text-sm font-medium uppercase tracking-wide text-indigo-200">
          <Sparkles className="size-4" aria-hidden /> {template ? "Summary (template)" : "AI assessment"}
        </h2>
        {replay ? (
          <span className="rounded-lg px-3 py-2 text-sm text-violet-100 ring-1 ring-violet-300/30">
            {template ? "No prepared card for this moment" : "Prepared before the replay"}
          </span>
        ) : (
          <button
            type="button"
            onClick={onRefresh}
            disabled={updating}
            className="flex h-11 items-center gap-1.5 rounded-lg px-3 text-base text-muted-foreground ring-1 ring-white/10 hover:bg-white/5 disabled:opacity-70"
          >
            <RefreshCw className={`size-4 ${updating ? "animate-spin" : ""}`} aria-hidden /> Refresh
          </button>
        )}
      </div>

      {updating && !replay && (
        <p role="status" className="flex items-center gap-2 text-sm text-indigo-100">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Asking Snowflake Cortex for a new assessment…
        </p>
      )}
      {template && a.fallback_reason && !updating && (
        <p className="flex items-start gap-2 rounded-lg bg-white/[0.04] px-3 py-2 text-sm text-muted-foreground">
          <Info className="mt-0.5 size-4 shrink-0" aria-hidden /> {a.fallback_reason}
        </p>
      )}

      <p className="text-xl font-semibold leading-snug">{a.headline}</p>
      <p className="text-base leading-relaxed">{a.assessment}</p>

      {a.possible_concerns.length > 0 && (
        <div>
          <h3 className="mb-1.5 text-base font-semibold">Possible concerns</h3>
          <ul className="flex flex-wrap gap-2">
            {a.possible_concerns.map((c) => (
              <li key={c} className="rounded-lg bg-amber-400/10 px-2.5 py-1 text-sm text-amber-50 ring-1 ring-amber-300/20">
                {c}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <h3 className="mb-1.5 text-base font-semibold">Consider checking</h3>
        <ul className="space-y-1.5">
          {a.recommended_checks.map((c, i) => (
            <li key={c}>
              <label className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-2 hover:bg-white/5">
                <input
                  type="checkbox"
                  className="size-5 shrink-0 accent-teal-400"
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
                  <p className="text-sm text-muted-foreground">Care plan · {c.section}</p>
                  <p className="text-base">“{c.quote}”</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="pl-2 text-base text-muted-foreground">
              {template
                ? "Care-plan citations come with the Snowflake Cortex assessment (Cortex Search over the care plan)."
                : "No care-plan lines were cited."}
            </p>
          ))}
      </div>

      <p className="text-sm text-muted-foreground">
        {template ? "Deterministic template from Vigil's scoring factors" : `Snowflake Cortex · ${a.model}`} · {at} ·
        confidence {a.confidence}
        <br />
        {a.caveats}
      </p>
    </section>
  );
}
