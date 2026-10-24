"use client";

import { ChevronDown, Loader2 } from "lucide-react";
import { useMemo, useState, useSyncExternalStore } from "react";

import { MarkCheckedForm } from "@/components/mark-checked-form";
import { AddNoteForm } from "@/components/resident/add-note-form";
import { AiNote, BedsideChecks } from "@/components/resident/assessment-card";
import { EventsTimeline } from "@/components/resident/events-timeline";
import { News2Table } from "@/components/resident/news2-table";
import { ResidentHeader } from "@/components/resident/resident-header";
import { ScoreBreakdown } from "@/components/resident/score-breakdown";
import { VitalsCharts } from "@/components/resident/vitals-charts";
import { VitalsTonight } from "@/components/resident/vitals-tonight";
import { useResident } from "@/hooks/use-resident";
import type { CodeStatus, ResidentDetail } from "@/lib/types";
import { useLiveStore } from "@/store/live";

export function ResidentView({ id }: { id: number }) {
  const { data, error, refreshing, reload, refresh } = useResident(id);
  // The live queue (every 2–10 s) is fresher than the 15 s detail poll for tier, rank and the plain reason.
  const queue = useLiveStore((s) => s.queue);
  const live = useMemo(() => {
    const i = queue?.items.findIndex((c) => c.id === id) ?? -1;
    return i < 0 ? null : { card: queue!.items[i], rank: i + 1 };
  }, [queue, id]);
  const [noting, setNoting] = useState(false);

  if (!data) {
    return (
      <main className="mx-auto flex w-full max-w-[80rem] flex-1 items-center justify-center gap-3 p-8 text-lg text-muted-foreground">
        {error ? (
          <p role="alert">Could not load resident {id}: {error}</p>
        ) : (
          <>
            <Loader2 className="size-6 animate-spin" aria-hidden /> Loading resident…
          </>
        )}
      </main>
    );
  }

  const cur = data.current;
  const tier = live?.card.tier ?? cur?.tier ?? null;
  const rank = live?.rank ?? cur?.rank ?? null;
  const her = data.sex === "M" ? "his" : "her";
  const reason = live?.card.plain_reason ?? cur?.plain_reason ?? "Resting as usual.";
  const first = data.name.split(" ")[0];
  const reload800 = () => setTimeout(reload, 800);

  return (
    <main className="mx-auto w-full max-w-[80rem] flex-1 px-4 pt-5 pb-16 sm:px-6">
      <ResidentHeader r={data} tier={tier} rank={rank} />

      <div className="mt-6 grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <div className="grid min-w-0 gap-6">
          <section className="vg-card px-6 py-[22px]" aria-labelledby="why-heading">
            <h2 id="why-heading" className="vg-label mb-3">
              Why now
            </h2>
            <p className="text-xl leading-snug sm:text-[21px]">{reason}</p>
            {cur?.concern?.summary && (
              <p className="mt-3 text-lg">
                Latest note: <span className="text-muted-foreground">“{cur.concern.summary}”</span>
              </p>
            )}
            {cur && cur.factors.length > 0 && (
              <p className="mt-3 text-[15px] text-muted-foreground">{cur.factors.map((f) => f.text).join(" · ")}</p>
            )}
            {cur?.task && <p className="mt-3 text-muted-foreground"><b className="text-foreground">Task:</b> {cur.task}</p>}
            <p className="mt-2.5 text-sm text-soft">
              {her === "his" ? "His" : "Her"} usual range comes from {her} own readings over the past week. Exact numbers
              are in the vitals below.
            </p>
          </section>

          <BedsideChecks a={data.assessment} asOf={data.as_of} her={her} />

          <section className="vg-card px-6 py-[22px]" aria-labelledby="change-heading">
            <h2 id="change-heading" className="vg-label mb-3">
              If you find a change
            </h2>
            <p className="text-lg">
              {data.assessment.escalation}
              {!CODE_MENTIONED.test(data.assessment.escalation) && ` ${codeSentence(data.code_status, data.sex)}`}
            </p>
            <div className="mt-[18px] flex flex-wrap items-center gap-3">
              <MarkCheckedForm residentId={id} name={data.name} onLogged={reload800} compact />
              <button
                type="button"
                className="vg-btn-secondary"
                aria-expanded={noting}
                onClick={() => setNoting(!noting)}
                disabled={!!data.replay}
                title={data.replay ? "Replay is read-only: notes are added in live mode" : undefined}
              >
                Add a note (voice or text)
              </button>
            </div>
            {noting && !data.replay && (
              <div className="mt-4">
                <AddNoteForm residentId={id} onSaved={reload800} />
              </div>
            )}
          </section>
        </div>

        <VitalsTonight residentId={id} baselines={data.baselines.vitals} her={her} />
      </div>

      <Details title={`Details: charts, scores and timeline for ${first}`}>
        <VitalsCharts
          residentId={id}
          baselines={data.baselines.vitals}
          copd={data.flags.includes("copd")}
          pronoun={her === "his" ? "His" : "Her"}
        />
        <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
          <div className="grid min-w-0 gap-6">
            {cur && (
              <ScoreBreakdown
                c={cur.components}
                score={cur.score}
                ruleText={cur.rule?.text ?? null}
                news2ForcesHigh={cur.news2.forces_high}
              />
            )}
            {cur && <News2Table n={cur.news2} />}
          </div>
          <div className="grid min-w-0 gap-6">
            <AiNote a={data.assessment} asOf={data.as_of} onRefresh={refresh} refreshing={refreshing} replay={!!data.replay} />
            <About r={data} />
            <EventsTimeline events={data.events} />
          </div>
        </div>
      </Details>
    </main>
  );
}

/** The escalation text already states the code status (the care plan's words): don't say it twice. */
const CODE_MENTIONED = /full code|\bDNR\b|\bDNI\b|comfort|POLST|code status/i;

/** Code status in plain words; it changes what to do, never the score. */
function codeSentence(code: CodeStatus, sex: "F" | "M"): string {
  const she = sex === "M" ? "He" : "She";
  return {
    FULL: `${she} is full code.`,
    DNR: `${she} is DNR: no CPR.`,
    "DNR-DNI": `${she} is DNR · DNI: no CPR and no breathing tube.`,
    COMFORT: `${she} is on comfort care: follow ${sex === "M" ? "his" : "her"} POLST and focus on comfort.`,
  }[code];
}

function About({ r }: { r: ResidentDetail }) {
  return (
    <section className="vg-card p-5" aria-labelledby="about-heading">
      <h3 id="about-heading" className="vg-label mb-3">
        About {r.name.split(" ")[0]}
      </h3>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3">
        <Item label="Mobility" value={r.mobility} />
        <Item label="Morse fall scale" value={`${r.morse}${r.morse >= 45 ? " · high fall risk" : ""}`} />
        <Item label="Cognition" value={r.cognition} />
        <Item label="Age" value={`${r.age} · ${r.sex === "F" ? "female" : "male"}`} />
        <div className="col-span-2">
          <dt className="text-sm text-muted-foreground">Diagnoses</dt>
          <dd>{r.diagnoses.join(" · ") || "—"}</dd>
        </div>
        {r.medications?.length > 0 && (
          <div className="col-span-2">
            <dt className="text-sm text-muted-foreground">Medications</dt>
            <dd>
              {r.medications.map((m, i) => (
                <span key={m.name}>
                  {i > 0 && " · "}
                  {m.name}
                  {m.flags.length > 0 && (
                    <span className="ml-1 rounded bg-high-bg px-1.5 text-sm text-high">{m.flags.join(", ").replace("_", "-")}</span>
                  )}
                </span>
              ))}
            </dd>
          </div>
        )}
      </dl>
    </section>
  );
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="first-letter:uppercase">{value}</dd>
    </div>
  );
}

// Details: open on desktop (sponsor judges), collapsed on phones; one tap either way.
const WIDE = "(min-width: 1024px)";
const subscribe = (cb: () => void) => {
  const mq = window.matchMedia(WIDE);
  mq.addEventListener("change", cb);
  return () => mq.removeEventListener("change", cb);
};

function Details({ title, children }: { title: string; children: React.ReactNode }) {
  const wide = useSyncExternalStore(subscribe, () => window.matchMedia(WIDE).matches, () => true);
  const [chosen, setChosen] = useState<boolean | null>(null);
  const open = chosen ?? wide;
  return (
    <section id="details" className="mt-8 scroll-mt-4" aria-label="Details">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setChosen(!open)}
        className="flex min-h-12 w-full items-center justify-between gap-3 rounded-[14px] border border-line bg-surface px-5 text-left text-lg font-bold hover:bg-surface-2"
      >
        {title}
        <ChevronDown className={`size-5 shrink-0 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden />
      </button>
      {open && <div className="mt-6 grid gap-6">{children}</div>}
    </section>
  );
}
