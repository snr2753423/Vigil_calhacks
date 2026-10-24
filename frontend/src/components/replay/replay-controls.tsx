"use client";

import { Bookmark, Pause, Play, Radio } from "lucide-react";
import { useState } from "react";
import { cn } from "cn";

import { postJSON } from "@/lib/api";
import { facilityClock12 } from "@/lib/format";
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
  const playing = brief?.playing ?? false;
  const speed = brief?.speed ?? SPEEDS[1];
  if (compact) {
    // The replay strip: Play, one speed chip that steps 30× → 60× → 120×, Back to live.
    const nextSpeed = SPEEDS[(SPEEDS.indexOf(speed as (typeof SPEEDS)[number]) + 1) % SPEEDS.length];
    return (
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled={busy} className="vg-chip"
          onClick={() => (playing ? run("Paused", "pause") : run("Playing", "start"))}>
          {playing ? "Pause" : "Play"}
        </button>
        <button type="button" disabled={busy} className="vg-chip" title={`Speed: ${speed}× (tap for ${nextSpeed}×)`}
          aria-label={`Speed ${speed}×, change to ${nextSpeed}×`} onClick={() => run(`Speed ${nextSpeed}×`, "speed", { speed: nextSpeed })}>
          {speed}×
        </button>
        <button type="button" disabled={busy} className="vg-chip" onClick={() => run("Back to live", "stop")}>
          Back to live
        </button>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={busy}
        onClick={() => (playing ? run("Paused", "pause") : run("Playing", "start"))}
        className={playing ? "vg-btn-secondary" : "vg-btn-primary"}
      >
        {playing ? <Pause className="size-5" aria-hidden /> : <Play className="size-5" aria-hidden />}
        {playing ? "Pause" : "Play"}
      </button>
      <div role="group" aria-label="Speed" className="flex gap-1">
        {SPEEDS.map((s) => (
          <button
            key={s}
            type="button"
            disabled={busy}
            aria-pressed={brief?.speed === s}
            onClick={() => run(`Speed ${s}×`, "speed", { speed: s })}
            className="vg-chip"
          >
            {s}×
          </button>
        ))}
      </div>
      <button type="button" disabled={busy} onClick={() => run("Back to live", "stop")} className="vg-btn-secondary">
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
                onResult?.(r.error ? { ok: false, text: `Jump failed: ${r.error}` } : { ok: true, text: `Jumped to ${facilityClock12(t)} · ${b.label}` });
              }}
              className={cn("vg-chip text-left text-[15px]", passed && "text-muted-foreground")}
            >
              <Bookmark className="size-4 shrink-0" aria-hidden />
              <span className="font-bold">{facilityClock12(t)}</span>
              <span>{b.label}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
