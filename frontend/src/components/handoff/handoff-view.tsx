"use client";

import { AlertOctagon, CheckCircle2, ClipboardList, Eye, Loader2, Printer, WatchIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { cn } from "cn";

import { getJSON, postJSON } from "@/lib/api";
import { facilityDayHHMM, facilityHHMM } from "@/lib/format";
import type { Handoff, HandoffResponse, ReplayStatus, Shift } from "@/lib/types";

const SHIFTS: { key: Shift; label: string; hours: string }[] = [
  { key: "day", label: "Day", hours: "07:00–15:00" },
  { key: "evening", label: "Evening", hours: "15:00–23:00" },
  { key: "night", label: "Night", hours: "23:00–07:00" },
];

/** Shift handoff (SPEC §9 ⑦, §11.3): pick a shift → Generate (Snowflake AI_AGG, SBAR) → print. */
export function HandoffView() {
  const [shift, setShift] = useState<Shift>("night");
  const [date, setDate] = useState<string>(""); // set after mount: the recorded night, else the latest shift
  const [replayNight, setReplayNight] = useState<string | null>(null);
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // The recorded night is the one the demo talks about; otherwise the API's default (the latest night shift that
    // has started, in facility time).
    const apiDefault = () =>
      getJSON<HandoffResponse>("/api/handoff?shift=night")
        .then((r) => setDate(r.date))
        .catch(() => undefined);
    getJSON<ReplayStatus>("/api/replay")
      .then((s) => {
        if (!s.night?.date) return apiDefault();
        setReplayNight(s.night.date);
        setDate(s.night.date);
      })
      .catch(apiDefault);
  }, []);

  const load = useCallback(async () => {
    if (!date) return;
    setLoading(true);
    setError(null);
    try {
      const r = await getJSON<HandoffResponse>(`/api/handoff?shift=${shift}&date=${date}`);
      setHandoff(r.handoff);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load");
    } finally {
      setLoading(false);
    }
  }, [shift, date]);

  useEffect(() => {
    const t = setTimeout(load, 0);
    return () => clearTimeout(t);
  }, [load]);

  const generate = async () => {
    setGenerating(true);
    setError(null);
    try {
      const r = await postJSON<HandoffResponse>("/api/handoff", { shift, date });
      setHandoff(r.handoff);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not generate");
    } finally {
      setGenerating(false);
    }
  };

  return (
    <main className="print-sheet mx-auto w-full max-w-[72rem] flex-1 space-y-4 px-3 py-4 sm:px-4">
      <div className="print-hide flex flex-wrap items-end gap-3 vg-card p-5">
        <div className="mr-auto">
          <h1 className="flex items-center gap-2 text-2xl font-bold">
            <ClipboardList className="size-6" aria-hidden /> Shift handoff
          </h1>
          <p className="text-base text-muted-foreground">
            SBAR summary of a shift&apos;s alerts, checks, notes and score changes, written by Snowflake AI_AGG.
          </p>
        </div>
        <div role="group" aria-label="Shift" className="flex rounded-xl bg-surface-2 p-1">
          {SHIFTS.map((s) => (
            <button
              key={s.key}
              type="button"
              aria-pressed={shift === s.key}
              onClick={() => setShift(s.key)}
              className={cn(
                "h-11 rounded-lg px-4 text-base ",
                shift === s.key ? "bg-surface font-bold text-foreground shadow-[var(--shadow)]" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {s.label} <span className="hidden text-sm text-muted-foreground sm:inline">{s.hours}</span>
            </button>
          ))}
        </div>
        <label className="flex flex-col gap-1 text-sm text-muted-foreground">
          Shift starts on
          <input
            type="date"
            value={date}
            onChange={(e) => e.target.value && setDate(e.target.value)}
            className="h-11 rounded-lg border border-input bg-background/60 px-3 text-base text-foreground"
          />
        </label>
        {replayNight && date !== replayNight && (
          <button
            type="button"
            onClick={() => {
              setShift("night");
              setDate(replayNight);
            }}
            className="h-11 rounded-xl bg-surface-2 px-4 text-base hover:bg-surface-2"
          >
            Recorded night ({replayNight})
          </button>
        )}
        <button
          type="button"
          onClick={generate}
          disabled={generating || !date}
          className="flex h-11 items-center gap-2 rounded-xl bg-surface-2 px-5 text-base font-bold text-accent ring-1 ring-accent hover:bg-surface-2 disabled:opacity-60"
        >
          {generating ? <Loader2 className="size-5 animate-spin" aria-hidden /> : <ClipboardList className="size-5" aria-hidden />}
          {generating ? "Snowflake AI_AGG is writing…" : handoff ? "Generate again" : "Generate"}
        </button>
        {handoff && (
          <button
            type="button"
            onClick={() => window.print()}
            className="flex h-11 items-center gap-2 rounded-xl bg-surface-2 px-4 text-base hover:bg-surface-2"
          >
            <Printer className="size-5" aria-hidden /> Print
          </button>
        )}
      </div>

      {error && (
        <p className="rounded-xl bg-crit-bg px-4 py-3 text-base text-crit" role="alert">
          {error}
        </p>
      )}
      {!handoff ? (
        <div className="flex h-48 items-center justify-center rounded-2xl bg-card text-lg text-muted-foreground border border-line">
          {loading ? (
            <Loader2 className="size-6 animate-spin" aria-label="Loading" />
          ) : generating ? (
            "Gathering the shift from Tiger and summarising it in Snowflake (about 10–20 s)…"
          ) : (
            "No handoff for this shift yet. Press Generate."
          )}
        </div>
      ) : (
        <HandoffSheet h={handoff} />
      )}
    </main>
  );
}

function HandoffSheet({ h }: { h: Handoff }) {
  const start = Date.parse(h.start);
  const end = Date.parse(h.end);
  const ai = h.model.includes("AI_AGG");
  return (
    <>
      <header className="space-y-1 vg-card p-5">
        <h2 className="text-xl font-bold">
          {h.shift[0].toUpperCase() + h.shift.slice(1)} shift · {facilityDayHHMM(start)} → {facilityDayHHMM(end)}
          {!h.complete && " (in progress)"}
        </h2>
        <p className="text-base text-muted-foreground">
          Sunrise Gardens Skilled Nursing Facility · {h.residents_flagged} residents needed attention · generated{" "}
          {facilityDayHHMM(Date.parse(h.generated_at))}
        </p>
        <p className={cn("flex items-center gap-1.5 text-sm", ai ? "text-muted-foreground" : "text-high")}>
          <ClipboardList className="size-4" aria-hidden />
          {ai
            ? `Snowflake Cortex AI_AGG · ${(h.ms / 1000).toFixed(1)} s · ${h.input_rows.length} shift rows + ${h.sbar_rows ?? 0} resident records`
            : `Deterministic summary (Snowflake unavailable: ${h.fallback_reason ?? "no response"})`}
          {h.fixes?.length ? ` · ${h.fixes.length} SBAR line(s) from the template` : ""}
        </p>
        <p className="text-sm text-muted-foreground">
          Decision support: confirm at the bedside. Factors come from Vigil&apos;s deterministic scores; AI_AGG only
          summarises them.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section icon={AlertOctagon} title="Critical events" tone="red" empty="No critical events.">
          {h.critical_events.map((c, i) => (
            <li key={i} className="flex gap-3">
              <span className="w-12 shrink-0 tabular-nums text-muted-foreground">{c.time}</span>
              <span>
                <b>{c.resident}</b> · {c.text}
              </span>
            </li>
          ))}
        </Section>
        <Section icon={Eye} title="Residents to watch next shift" tone="amber" empty="Nobody flagged at shift end.">
          {h.watch_next_shift.map((w, i) => (
            <li key={i}>
              <b>{w.resident}</b> · {w.reason}
              {w.action && <span className="block text-muted-foreground">→ {w.action}</span>}
            </li>
          ))}
        </Section>
        <Section icon={CheckCircle2} title="Resolved" tone="green" empty="Nothing resolved this shift.">
          {h.resolved.map((r, i) => (
            <li key={i}>
              <b>{r.resident}</b> · {r.text}
            </li>
          ))}
        </Section>
        <Section icon={WatchIcon} title="Device issues" tone="slate" empty="No device issues.">
          {h.device_issues.map((d, i) => (
            <li key={i}>
              <b>{d.resident}</b> · {d.text}
            </li>
          ))}
        </Section>
      </div>
      <Section icon={ClipboardList} title="Follow-ups for the next shift" tone="slate" empty="No follow-ups mentioned.">
        {h.follow_ups.map((f, i) => (
          <li key={i}>{f}</li>
        ))}
      </Section>

      <section aria-labelledby="sbar-heading" className="space-y-3 vg-card p-5">
        <h2 id="sbar-heading" className="text-xl font-bold">
          By resident (SBAR)
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[44rem] border-collapse text-left text-base">
            <thead className="text-sm text-muted-foreground">
              <tr className="border-b border-line">
                <th className="py-2 pr-3 ">Resident</th>
                <th className="py-2 pr-3 ">Situation</th>
                <th className="py-2 pr-3 ">Background</th>
                <th className="py-2 pr-3 ">Assessment</th>
                <th className="py-2 ">Recommendation</th>
              </tr>
            </thead>
            <tbody>
              {h.residents.map((r) => (
                <tr key={r.resident_id} className="border-b border-line align-top">
                  <td className="py-2 pr-3 font-bold">
                    <Link href={`/resident/${r.resident_id}`} className="hover:underline">
                      {r.name}
                    </Link>
                    <span className="block text-sm font-normal text-muted-foreground">
                      Room {r.room}
                      {r.source === "template" ? " · template" : ""}
                    </span>
                  </td>
                  <td className="py-2 pr-3">{r.situation}</td>
                  <td className="py-2 pr-3 text-muted-foreground">{r.background}</td>
                  <td className="py-2 pr-3">{r.assessment}</td>
                  <td className="py-2">{r.recommendation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <details className="print-hide vg-card p-5">
        <summary className="cursor-pointer text-base ">What Snowflake was given ({h.input_rows.length} rows)</summary>
        <ol className="mt-3 space-y-2 text-sm text-muted-foreground">
          {h.input_rows.map((r, i) => (
            <li key={i} className="rounded-lg bg-surface-2 p-2 font-mono">
              {r}
            </li>
          ))}
        </ol>
        <p className="mt-2 text-sm text-muted-foreground">
          Times are facility time ({facilityHHMM(start)}–{facilityHHMM(end)}). Scores come from{" "}
          {h.source === "replay_snapshots" ? "the prepared replay night" : h.source === "mixed" ? "the prepared replay night and live scoring" : "live scoring"}.
        </p>
      </details>
    </>
  );
}

const TONES = {
  red: "text-crit",
  amber: "text-high",
  green: "text-stable",
  slate: "text-muted-foreground",
} as const;

function Section({
  icon: Icon,
  title,
  tone,
  empty,
  children,
}: {
  icon: typeof AlertOctagon;
  title: string;
  tone: keyof typeof TONES;
  empty: string;
  children: React.ReactNode[];
}) {
  return (
    <section className="space-y-2 vg-card p-5">
      <h2 className={cn("flex items-center gap-2 text-lg font-bold", TONES[tone])}>
        <Icon className="size-5" aria-hidden /> {title}
      </h2>
      {children.length ? <ul className="space-y-2 text-base">{children}</ul> : <p className="text-base text-muted-foreground">{empty}</p>}
    </section>
  );
}
