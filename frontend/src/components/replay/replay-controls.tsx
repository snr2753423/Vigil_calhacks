"use client";

import { Bookmark, Pause, Play, Radio } from "lucide-react";
import { useState } from "react";
import { cn } from "cn";

import { postJSON } from "@/lib/api";
import { facilityHHMM } from "@/lib/format";
import type { Bookmark as BookmarkT, ReplayStatus } from "@/lib/types";
import { useLiveStore } from "@/store/live";

export const SPEEDS = [30, 60, 120] as const;

type Result = { ok: boolean; text: string };

/** POST to a replay endpoint; returns the new status or the error text. */
export async function replayCall(path: string, body?: unknown): Promise<{ status?: ReplayStatus; error?: string }> {
  try {
    return { status: await postJSON<ReplayStatus>(`/api/replay/${path}`, body) };
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
}

/** Play/pause, speed and back-to-live: used by the replay banner (compact) and /admin. */
export function PlaybackButtons({ compact = false, onResult }: { compact?: boolean; onResult?: (r: Result) => void }) {
  const brief = useLiveStore((s) => s.replay);
  const [busy, setBusy] = useState(false);
  const run = async (label: string, path: string, body?: unknown) => {
    setBusy(true);
    const r = await replayCall(path, body);
    setBusy(false);
    onResult?.(r.error ? { ok: false, text: `${label} failed: ${r.error}` } : { ok: true, text: label });
  };
  const h = compact ? "h-10 px-3 text-sm" : "h-12 px-4 text-base";
  const playing = brief?.playing ?? false;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={busy}
        onClick={() => (playing ? run("Paused", "pause") : run("Playing", "start"))}
        className={cn("flex items-center gap-2 rounded-xl font-semibold disabled:opacity-50", h,
          playing ? "bg-white/10 hover:bg-white/15" : "bg-violet-400 text-violet-950 hover:bg-violet-300")}
      >
        {playing ? <Pause className="size-5" aria-hidden /> : <Play className="size-5" aria-hidden />}
        {playing ? "Pause" : "Play"}
      </button>
      <div role="group" aria-label="Speed" className="flex rounded-xl bg-white/5 p-1">
        {SPEEDS.map((s) => (
          <button
            key={s}
            type="button"
            disabled={busy}
            aria-pressed={brief?.speed === s}
            onClick={() => run(`Speed ${s}×`, "speed", { speed: s })}
            className={cn("rounded-lg px-3 font-mono font-semibold", compact ? "h-8 text-sm" : "h-10 text-base",
              brief?.speed === s ? "bg-white/15 text-foreground" : "text-muted-foreground hover:text-foreground")}
          >
            {s}×
          </button>
        ))}
      </div>
      <button
        type="button"
        disabled={busy}
        onClick={() => run("Back to live", "stop")}
        className={cn("flex items-center gap-2 rounded-xl bg-teal-500/15 font-semibold text-teal-100 ring-1 ring-teal-400/40 hover:bg-teal-500/25 disabled:opacity-50", h)}
      >
        <Radio className="size-5" aria-hidden /> Back to live
      </button>
    </div>
  );
}

/** One button per bookmark (SPEC §4); jumping keeps the current play/pause state. */
export function BookmarkButtons({ bookmarks, cursor, onResult }: {
  bookmarks: BookmarkT[];
  cursor: number | null;
  onResult?: (r: Result) => void;
}) {
  const [busy, setBusy] = useState<number | null>(null);
  return (
    <ul className="flex flex-wrap gap-2">
      {bookmarks.map((b, i) => {
        const t = Date.parse(b.ts);
        const passed = cursor !== null && cursor >= t;
        return (
          <li key={b.ts + b.label}>
            <button
              type="button"
              disabled={busy !== null}
              onClick={async () => {
                setBusy(i);
                const r = await replayCall("seek", { bookmark: i });
                setBusy(null);
                onResult?.(r.error ? { ok: false, text: `Jump failed: ${r.error}` } : { ok: true, text: `Jumped to ${facilityHHMM(t)} · ${b.label}` });
              }}
              className={cn(
                "flex h-11 items-center gap-2 rounded-lg px-3 text-left text-sm ring-1 disabled:opacity-60",
                passed ? "bg-violet-500/15 text-violet-50 ring-violet-300/40" : "ring-white/15 hover:bg-white/5",
              )}
            >
              <Bookmark className="size-4 shrink-0" aria-hidden />
              <span className="font-mono tabular-nums">{facilityHHMM(t)}</span>
              <span>{b.label}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
