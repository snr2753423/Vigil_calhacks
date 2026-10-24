"use client";

import { Moon, Radio } from "lucide-react";

import { ConnectionBadge } from "@/components/connection-badge";
import { Badge } from "@/components/ui/badge";
import { useNow } from "@/hooks/use-now";
import { FACILITY_SHORT_NAME, formatFacilityClock } from "@/lib/facility";
import { useLiveStore } from "@/store/live";

export function AppHeader() {
  const now = useNow();
  const sim = useLiveStore((s) => s.sim);
  const clock = now === null ? "--:--:--" : formatFacilityClock(now, sim?.display_clock_offset_s);

  return (
    <header className="sticky top-0 z-10 border-b border-border/60 bg-background/85 backdrop-blur">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
        <div className="flex items-baseline gap-3">
          <span className="text-xl font-semibold tracking-tight text-teal-300">Vigil</span>
          <span className="text-sm text-muted-foreground">{FACILITY_SHORT_NAME}</span>
        </div>
        <div className="ml-auto flex items-center gap-3">
          {sim?.force_night && (
            <Badge variant="secondary" className="h-7 gap-1.5 px-3 text-sm">
              <Moon aria-hidden /> Night mode
            </Badge>
          )}
          <Badge variant="outline" className="h-7 gap-1.5 px-3 text-sm uppercase">
            <Radio aria-hidden /> {sim?.mode ?? "live"}
          </Badge>
          <ConnectionBadge />
          <time className="min-w-[5.5rem] text-right font-mono text-lg tabular-nums" suppressHydrationWarning>
            {clock}
          </time>
        </div>
      </div>
    </header>
  );
}
