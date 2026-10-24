"use client";

import { Keyboard, X } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "cn";

import { SPEEDS } from "@/components/replay/replay-controls";
import { getJSON, postJSON } from "@/lib/api";
import { facilityHHMM } from "@/lib/format";
import { actionFor, ignoreKey, nextSpeed, SHORTCUTS, type ShortcutAction } from "@/lib/shortcuts";
import type { Bookmark, ReplayStatus } from "@/lib/types";
import { useLiveStore } from "@/store/live";

const LIVE_RESIDENT = 120;
const G_WINDOW_MS = 1200;

/** Hidden presenter controls: "?" shows the panel; the keys work with it closed too (not on the iPhone bed page). */
export function DemoShortcuts() {
  const router = useRouter();
  const bedPage = usePathname().startsWith("/bed/");
  const [open, setOpen] = useState(false);
  const [toast, setToast] = useState<{ ok: boolean; text: string; at: number } | null>(null);
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);
  const gAt = useRef(0);
  const busy = useRef(false);

  const loadBookmarks = useCallback(async (): Promise<Bookmark[]> => {
    try {
      const s = await getJSON<ReplayStatus>("/api/replay");
      setBookmarks(s.bookmarks);
      return s.bookmarks;
    } catch {
      return [];
    }
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2500);
    return () => clearTimeout(t);
  }, [toast]);

  const run = useCallback(
    async (action: ShortcutAction) => {
      const say = (ok: boolean, text: string) => setToast({ ok, text, at: Date.now() });
      if (action.kind === "toggle_panel") {
        if (!open) void loadBookmarks(); // fresh bookmarks each time it opens (they follow the seeded night)
        return setOpen(!open);
      }
      if (action.kind === "close_panel") return setOpen(false);
      if (action.kind === "goto") return router.push(action.href);
      if (busy.current) return;
      busy.current = true;
      const { sim, replay } = useLiveStore.getState();
      const replaying = sim?.mode === "replay";
      const call = async (label: string, path: string, body?: unknown) => {
        try {
          await postJSON(path, body);
          say(true, label);
        } catch (e) {
          say(false, `${label} failed: ${e instanceof Error ? e.message : String(e)}`);
        }
      };
      try {
        switch (action.kind) {
          case "play_pause":
            if (replaying && replay?.playing) await call("Replay paused", "/api/replay/pause");
            else await call(replaying ? "Replay playing" : "Replay started", "/api/replay/start");
            break;
          case "bookmark": {
            const marks = bookmarks.length ? bookmarks : await loadBookmarks();
            const b = marks[action.index];
            if (!b) {
              say(false, `No bookmark ${action.index + 1}`);
              break;
            }
            const label = `${facilityHHMM(Date.parse(b.ts))} · ${b.label}`;
            if (replaying) await call(label, "/api/replay/seek", { bookmark: action.index });
            else await call(`${label} (paused: Space plays)`, "/api/replay/start", { at: b.ts, play: false });
            break;
          }
          case "speed": {
            const s = nextSpeed(replay?.speed, SPEEDS);
            await call(`Speed ${s}×`, "/api/replay/speed", { speed: s });
            break;
          }
          case "live":
            await call("Back to live", "/api/replay/stop");
            break;
          case "force_night":
            await call(sim?.force_night ? "Night mode off" : "Night mode on", "/api/sim/force_night", {
              enabled: !sim?.force_night,
              display_clock_offset_s: sim?.display_clock_offset_s ?? 0,
            });
            break;
          case "bed_exit":
            await call("Bed exit recorded for R-120", "/api/sim/inject", { kind: "bed_exit", resident_id: LIVE_RESIDENT });
            break;
          case "bed_return":
            await call("R-120 back in bed", `/api/devices/bed/${LIVE_RESIDENT}/state`, { in_bed: true });
            break;
        }
      } finally {
        busy.current = false;
      }
    },
    [bookmarks, loadBookmarks, open, router],
  );

  useEffect(() => {
    if (bedPage) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat || ignoreKey(e, e.target)) return;
      if (e.key === "g" && !e.shiftKey) {
        gAt.current = Date.now();
        return;
      }
      const afterG = Date.now() - gAt.current < G_WINDOW_MS;
      gAt.current = 0;
      const action = actionFor(e, afterG);
      if (!action) return;
      if (action.kind === "close_panel" && !open) return; // leave Escape to whatever else is open
      // Space would otherwise scroll the page or press the focused button.
      if (e.key === " " && (e.target as HTMLElement | null)?.tagName?.toLowerCase() === "button") return;
      e.preventDefault();
      void run(action);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [bedPage, open, run]);

  if (bedPage) return null;
  return (
    <>
      {open && (
        <div className="fixed inset-0 z-[60] flex items-start justify-center bg-background/80 p-4 pt-[10vh]" onClick={() => setOpen(false)}>
          <section
            role="dialog"
            aria-label="Demo shortcuts"
            onClick={(e) => e.stopPropagation()}
            className="max-h-[80vh] w-full max-w-xl overflow-y-auto vg-card p-5"
          >
            <header className="mb-3 flex items-center gap-2">
              <Keyboard className="size-5 text-accent" aria-hidden />
              <h2 className="text-lg font-bold">Demo shortcuts</h2>
              <button type="button" onClick={() => setOpen(false)} className="ml-auto flex size-11 items-center justify-center rounded-[10px] hover:bg-surface-2">
                <X className="size-5" aria-hidden />
                <span className="sr-only">Close</span>
              </button>
            </header>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-base">
              {SHORTCUTS.map((s) => (
                <div key={s.keys} className="contents">
                  <dt>
                    <kbd className="rounded-md bg-surface-2 px-2 py-0.5 font-mono text-sm">{s.keys}</kbd>
                  </dt>
                  <dd className="text-muted-foreground">{s.label}</dd>
                </div>
              ))}
            </dl>
            <h3 className="mt-4 mb-2 text-sm font-bold uppercase tracking-wide text-muted-foreground">Bookmarks</h3>
            {bookmarks.length === 0 ? (
              <p className="text-sm text-muted-foreground">No recorded night yet (run Prepare replay).</p>
            ) : (
              <ol className="space-y-1 text-base">
                {bookmarks.map((b, i) => (
                  <li key={b.ts + b.label} className="flex gap-3">
                    <kbd className="rounded-md bg-surface-2 px-2 py-0.5 font-mono text-sm">{i + 1}</kbd>
                    <span className="tabular-nums">{facilityHHMM(Date.parse(b.ts))}</span>
                    <span>{b.label}</span>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      )}
      {toast && (
        <p
          key={toast.at}
          role="status"
          className={cn(
            "fixed bottom-4 left-4 z-[70] rounded-full px-4 py-2 text-base shadow-[var(--shadow)] ring-1",
            toast.ok ? "bg-surface-2 text-foreground ring-line" : "bg-crit-bg text-crit ring-crit-solid",
          )}
        >
          {toast.text}
        </p>
      )}
    </>
  );
}
