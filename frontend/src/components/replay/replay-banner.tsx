"use client";

import { Rewind } from "lucide-react";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { BookmarkButtons, PlaybackButtons } from "@/components/replay/replay-controls";
import { useClock } from "@/hooks/use-clock";
import { useReplayStatus } from "@/hooks/use-replay-status";
import { formatFacilityClock } from "@/lib/facility";
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
    <div className="border-b border-violet-400/30 bg-violet-950/60" role="region" aria-label="Replay">
      <div className="mx-auto flex max-w-[96rem] flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2">
        <p className="flex min-w-0 items-center gap-2 text-base text-violet-50">
          <Rewind className="size-5 shrink-0 text-violet-200" aria-hidden />
          <span>
            <strong className="font-semibold">Replaying last night&apos;s recorded data through the live pipeline.</strong>{" "}
            <span className="text-violet-200/80">
              {brief.playing ? `${brief.speed}×` : "Paused"} · Snowflake results and AI cards were prepared ahead
            </span>
          </span>
        </p>
        <div className="flex items-center gap-2">
          <time className="font-mono text-lg tabular-nums text-violet-50" suppressHydrationWarning>
            {formatFacilityClock(cursor)}
          </time>
          <div
            className="h-2 w-28 overflow-hidden rounded-full bg-white/10 sm:w-40"
            role="progressbar"
            aria-label="Position in the night"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div className="h-full bg-violet-300" style={{ width: `${pct}%` }} />
          </div>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <PlaybackButtons compact onResult={(r) => setMsg(r.ok ? null : r)} />
          {status && status.bookmarks.length > 0 && (
            <button
              type="button"
              aria-expanded={marks}
              onClick={() => setMarks(!marks)}
              className="h-10 rounded-xl px-3 text-sm font-semibold text-violet-100 ring-1 ring-violet-300/40 hover:bg-white/5"
            >
              Bookmarks
            </button>
          )}
        </div>
        {msg && <p className="w-full text-sm text-red-200" role="alert">{msg.text}</p>}
        {marks && status && (
          <div className="w-full pb-1">
            <BookmarkButtons bookmarks={status.bookmarks} cursor={Date.parse(brief.cursor)} onResult={(r) => setMsg(r.ok ? null : r)} />
          </div>
        )}
      </div>
    </div>
  );
}
