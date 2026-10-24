import { AlertOctagon, BedDouble, CheckCircle2, Info, MessageSquareText } from "lucide-react";
import { cn } from "cn";

import { describeEvent, type EventView } from "@/lib/events";
import { facilityHHMM } from "@/lib/format";
import type { ResidentEvent } from "@/lib/types";

const TONE: Record<EventView["tone"], { icon: typeof Info; className: string; label: string }> = {
  alert: { icon: AlertOctagon, className: "text-crit bg-crit-bg", label: "Alert" },
  staff: { icon: MessageSquareText, className: "text-foreground bg-surface-2", label: "Staff note" },
  good: { icon: CheckCircle2, className: "text-stable bg-stable-bg", label: "Done" },
  info: { icon: Info, className: "text-muted-foreground bg-surface-2", label: "Event" },
};

const SHOWN = 25;

/** Last 24 h of events and notes, newest first, in real facility time (same as the chart axes). */
export function EventsTimeline({ events }: { events: ResidentEvent[] }) {
  return (
    <section aria-labelledby="events-heading" className="space-y-2 vg-card p-5">
      <h2 id="events-heading" className="text-xl font-bold">
        Last 24 hours
      </h2>
      {events.length === 0 ? (
        <p className="text-base text-muted-foreground">No events.</p>
      ) : (
        <ol className="space-y-1">
          {events.slice(0, SHOWN).map((e) => {
            const v = describeEvent(e);
            const tone = e.type === "bed_exit" || e.type === "bed_return" ? { ...TONE.info, icon: BedDouble } : TONE[v.tone];
            const Icon = tone.icon;
            return (
              <li key={`${e.ts}-${e.type}`} className="flex gap-3 rounded-lg px-1 py-1.5">
                <span className="w-14 shrink-0 pt-0.5 text-base tabular-nums text-muted-foreground">
                  {facilityHHMM(Date.parse(e.ts))}
                </span>
                <span className={cn("flex size-8 shrink-0 items-center justify-center rounded-lg", tone.className)}>
                  <Icon className="size-4" aria-label={tone.label} />
                </span>
                <div className="min-w-0">
                  <p className="text-base ">{v.title}</p>
                  {v.detail && <p className="text-base text-muted-foreground">{v.detail}</p>}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
