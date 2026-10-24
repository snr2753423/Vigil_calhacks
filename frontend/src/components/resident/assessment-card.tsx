"use client";

import { Info, Loader2, RefreshCw } from "lucide-react";
import { useState } from "react";

import { facilityClock12 } from "@/lib/format";
import type { Assessment } from "@/lib/types";

type Props = { a: Assessment; asOf: string; her: string };

/** "Worth checking at the bedside" (a checklist) and "From her care plan" (verbatim quotes) from the resident's
 *  AI card: a Snowflake Cortex assessment, or the deterministic template when none is fresh or Snowflake is
 *  slow/down (same shape, never blank). */
export function BedsideChecks(props: Props) {
  const { a } = props;
  // Remount the checklist when a new assessment arrives so old ticks don't carry over to new checks.
  return <ChecksBody key={`${a.source}|${a.as_of ?? ""}|${a.headline}`} {...props} />;
}

function ChecksBody({ a, asOf, her }: Props) {
  const [done, setDone] = useState<Set<number>>(new Set());
  const template = a.source === "template";
  return (
    <section className="vg-card px-6 py-[22px]" aria-labelledby="checks-heading">
      <h2 id="checks-heading" className="vg-label mb-3">
        Worth checking at the bedside
      </h2>
      <ul>
        {a.recommended_checks.map((c, i) => (
          <li key={c} className={i > 0 ? "border-t border-line" : undefined}>
            <label className="flex min-h-11 cursor-pointer items-start gap-3 py-2.5 text-lg">
              <input
                type="checkbox"
                className="mt-0.5 size-6 shrink-0 accent-[var(--accent)]"
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

      <h2 className="vg-label mt-[18px] mb-1">From {her} care plan</h2>
      {a.care_plan_citations.length ? (
        a.care_plan_citations.map((c) => (
          <blockquote key={c.quote} className="mt-2.5 rounded-r-[10px] border-l-[3px] border-accent bg-surface-2 px-3.5 py-2.5">
            “{c.quote}”<small className="mt-1 block text-sm text-soft">Care plan · {c.section}</small>
          </blockquote>
        ))
      ) : (
        <p className="mt-1 text-muted-foreground">
          {template
            ? "Care-plan quotes come with the Snowflake Cortex assessment (Cortex Search over the care plan)."
            : "No care-plan lines were cited."}
        </p>
      )}
      <p className="mt-2.5 text-sm text-soft">{aiSource(a, asOf, her)}</p>
    </section>
  );
}

export function aiSource(a: Assessment, asOf: string, her: string): string {
  const at = facilityClock12(Date.parse(a.as_of ?? asOf));
  return a.source === "cortex"
    ? `Suggested by Vigil (AI, from ${her} vitals and care plan) · ${at} · decision support, not a diagnosis · Snowflake Cortex · ${a.model}`
    : `Suggested by Vigil from ${her} scores (template: the AI card is not ready) · ${at} · decision support, not a diagnosis`;
}

type NoteProps = { a: Assessment; asOf: string; onRefresh: () => void; refreshing: boolean; replay: boolean };

/** The full AI note for Details: headline, assessment, possible concerns, confidence and caveats, plus Refresh. */
export function AiNote({ a, asOf, onRefresh, refreshing, replay }: NoteProps) {
  const template = a.source === "template";
  const updating = refreshing || a.pending === true;
  return (
    <section className="vg-card space-y-3 p-5" aria-labelledby="ai-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h3 id="ai-heading" className="vg-label">
          {template ? "Vigil's summary (template)" : "Vigil's AI note · Snowflake Cortex"}
        </h3>
        {replay ? (
          <span className="text-sm text-muted-foreground">
            {template ? "No prepared card for this moment" : "Prepared before the replay"}
          </span>
        ) : (
          <button type="button" onClick={onRefresh} disabled={updating} className="vg-chip">
            <RefreshCw className={`size-4 ${updating ? "animate-spin" : ""}`} aria-hidden /> Refresh
          </button>
        )}
      </div>
      {updating && !replay && (
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Asking Snowflake Cortex for a new assessment…
        </p>
      )}
      {template && a.fallback_reason && !updating && (
        <p className="flex items-start gap-2 rounded-[10px] bg-surface-2 px-3 py-2 text-sm text-muted-foreground">
          <Info className="mt-0.5 size-4 shrink-0" aria-hidden /> {a.fallback_reason}
        </p>
      )}
      <p className="text-lg font-bold">{a.headline}</p>
      <p>{a.assessment}</p>
      {a.possible_concerns.length > 0 && (
        <p className="text-muted-foreground">
          <b className="text-foreground">Possible concerns:</b> {a.possible_concerns.join(" · ")}
        </p>
      )}
      <p className="text-sm text-soft">
        {template ? "Deterministic template from Vigil's scoring factors" : `Snowflake Cortex · ${a.model}`} ·{" "}
        {facilityClock12(Date.parse(a.as_of ?? asOf))} · confidence {a.confidence}
        {a.caveats && (
          <>
            <br />
            {a.caveats}
          </>
        )}
      </p>
    </section>
  );
}
