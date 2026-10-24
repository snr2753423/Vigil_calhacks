"use client";

import { usePathname } from "next/navigation";
import { useState } from "react";

import { BookmarkButtons, PlaybackButtons } from "@/components/replay/replay-controls";
import { useClock } from "@/hooks/use-clock";
import { useReplayStatus } from "@/hooks/use-replay-status";
import { facilityClock12 } from "@/lib/format";
import { replayProgress } from "@/lib/replay";
import { useLiveStore } from "@/store/live";

/** Under the header on every page while replaying (SPEC §4): what this is, the replay clock, and controls. */
export function ReplayBanner() {
  const path = usePathname();
  const brief = useLiveStore((s) => (s.sim?.mode === "replay" ? s.replay : null));
  const { now } = useClock();
  const { status } = useReplayStatus(brief !== null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [marks, setMarks] = useState(false);
  if (!brief || path.startsWith("/bed/")) return null;
  const cursor = now ?? Date.parse(brief.cursor);
  const pct = Math.round(replayProgress(brief, cursor) * 1000) / 10;

  return (
    <div className="print-hide mx-auto w-full max-w-[80rem] px-4 sm:px-6" role="region" aria-label="Replay">
      <div className="mt-3.5 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-surface-2 px-4 py-2.5 text-muted-foreground">
        <p className="min-w-0">
          <strong className="font-bold text-foreground">Replaying last night</strong> through the live system ·{" "}
          {brief.playing ? `playing at ${brief.speed}×, now ` : "paused at "}
          <time className="text-foreground" suppressHydrationWarning>
            {facilityClock12(cursor)}
          </time>
          <span className="sr-only"> ({pct}% through the night)</span>
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <PlaybackButtons compact onResult={(r) => setMsg(r.ok ? null : r)} />
          {status && status.bookmarks.length > 0 && (
            <button type="button" aria-expanded={marks} onClick={() => setMarks(!marks)} className="vg-chip">
              Bookmarks
            </button>
          )}
        </div>
        {msg && (
          <p className="w-full text-sm text-crit" role="alert">
            {msg.text}
          </p>
        )}
        {marks && status && (
          <div className="w-full pb-1">
            <BookmarkButtons bookmarks={status.bookmarks} cursor={Date.parse(brief.cursor)} onResult={(r) => setMsg(r.ok ? null : r)} />
          </div>
        )}
      </div>
      <p className="sr-only">Snowflake results and AI cards for the replay were prepared ahead.</p>
    </div>
  );
}
