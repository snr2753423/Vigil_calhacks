"use client";

import { ClipboardCheck, Loader2 } from "lucide-react";
import { useMemo } from "react";

import { MarkCheckedForm } from "@/components/mark-checked-form";
import { AddNoteForm } from "@/components/resident/add-note-form";
import { AssessmentCard } from "@/components/resident/assessment-card";
import { EventsTimeline } from "@/components/resident/events-timeline";
import { News2Table } from "@/components/resident/news2-table";
import { ResidentHeader } from "@/components/resident/resident-header";
import { ScoreBreakdown } from "@/components/resident/score-breakdown";
import { VitalsCharts } from "@/components/resident/vitals-charts";
import { useResident } from "@/hooks/use-resident";
import { useLiveStore } from "@/store/live";

export function ResidentView({ id }: { id: number }) {
  const { data, error, refreshing, reload, refresh } = useResident(id);
  // The live queue (every 2–10 s) is fresher than the 15 s detail poll for tier/score/rank.
  const queue = useLiveStore((s) => s.queue);
  const live = useMemo(() => {
    const i = queue?.items.findIndex((c) => c.id === id) ?? -1;
    return i < 0 ? null : { card: queue!.items[i], rank: i + 1 };
  }, [queue, id]);

  if (!data) {
    return (
      <main className="mx-auto flex w-full max-w-[96rem] flex-1 items-center justify-center gap-3 p-8 text-lg text-muted-foreground">
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
  const score = live?.card.score ?? cur?.score ?? null;
  const pronoun = data.sex === "F" ? "Her" : "His";

  return (
    <main className="mx-auto w-full max-w-[96rem] flex-1 space-y-4 px-3 py-4 sm:px-4">
      <ResidentHeader r={data} tier={tier} score={score} rank={live?.rank ?? cur?.rank ?? null} />
      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 lg:grid-cols-[minmax(0,1fr)_26rem]">
        <div className="min-w-0 space-y-4">
          <AssessmentCard a={data.assessment} asOf={data.as_of} onRefresh={refresh} refreshing={refreshing} />
          <VitalsCharts
            residentId={id}
            baselines={data.baselines.vitals}
            copd={data.flags.includes("copd")}
            pronoun={pronoun}
          />
        </div>
        <div className="min-w-0 space-y-4">
          <section aria-labelledby="check-heading" className="space-y-2 rounded-2xl bg-card p-4 ring-1 ring-white/10">
            <h2 id="check-heading" className="flex items-center gap-2 text-xl font-semibold">
              <ClipboardCheck className="size-5" aria-hidden /> Log check
            </h2>
            <MarkCheckedForm residentId={id} name={data.name} onLogged={() => setTimeout(reload, 800)} />
          </section>
          {cur && (
            <ScoreBreakdown
              c={cur.components}
              score={cur.score}
              ruleText={cur.rule?.text ?? null}
              news2ForcesHigh={cur.news2.forces_high}
            />
          )}
          {cur && <News2Table n={cur.news2} />}
          <AddNoteForm residentId={id} onSaved={() => setTimeout(reload, 800)} />
          <EventsTimeline events={data.events} />
        </div>
      </div>
    </main>
  );
}
