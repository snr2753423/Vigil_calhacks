"use client";

import { Clock, Moon, Pause, Play, RotateCcw, WatchIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { cn } from "cn";

import { InjectorPanel } from "@/components/admin/injector-panel";
import { HeartbeatPanel } from "@/components/heartbeat-panel";
import { Stat } from "@/components/stat";
import { useNow } from "@/hooks/use-now";
import { getJSON, postJSON } from "@/lib/api";
import { offsetForDisplayTime } from "@/lib/display-clock";
import { formatFacilityClock } from "@/lib/facility";
import type { SimState, SimStatus } from "@/lib/types";
import { useLiveStore } from "@/store/live";

const POLL_MS = 5000;

/** Demo controls (SPEC §11.8): pause/start, force night + display clock, scenario injectors, reset. */
export function AdminPanel() {
  const [status, setStatus] = useState<SimStatus | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const liveSim = useLiveStore((s) => s.sim);
  const lastInjection = useLiveStore((s) => s.lastInjection);
  const sim: SimState | null = liveSim ?? status?.sim ?? null;

  const refresh = useCallback(async () => {
    try {
      setStatus(await getJSON<SimStatus>("/api/sim"));
    } catch {
      // backend restarting: the next poll retries
    }
  }, []);

  useEffect(() => {
    const first = setTimeout(refresh, 0);
    const t = setInterval(refresh, POLL_MS);
    return () => {
      clearTimeout(first);
      clearInterval(t);
    };
  }, [refresh, lastInjection]);

  const act = async (label: string, path: string, body?: unknown) => {
    try {
      await postJSON(path, body);
      setMsg({ ok: true, text: label });
      void refresh();
    } catch (e) {
      setMsg({ ok: false, text: `${label} failed: ${e instanceof Error ? e.message : e}` });
    }
  };

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 space-y-4 px-3 py-4 sm:px-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Admin · demo controls</h1>
        <p className="text-base text-muted-foreground">
          Stored data is always real time; night mode and the display clock only change what the night-time rules
          and the header clock see.
        </p>
      </div>
      {msg && (
        <p
          role="status"
          className={cn("rounded-xl px-4 py-3 text-base", msg.ok ? "bg-emerald-500/15 text-emerald-100" : "bg-red-500/15 text-red-100")}
        >
          {msg.text}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Simulator">
          <div className="flex flex-wrap items-center gap-2">
            {sim?.paused ? (
              <BigButton onClick={() => act("Simulator started", "/api/sim/start")} icon={Play} tone="teal">
                Start
              </BigButton>
            ) : (
              <BigButton onClick={() => act("Simulator paused", "/api/sim/pause")} icon={Pause}>
                Pause
              </BigButton>
            )}
            <span className="text-base text-muted-foreground">
              {sim?.paused ? "Paused: the queue is frozen." : "Running: residents stream every 5 s."}
            </span>
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-base sm:grid-cols-3">
            <Stat label="Live loop" value={status?.live_sim?.phase ?? "—"} />
            <Stat label="Residents streaming" value={String(status?.live_sim?.residents ?? "—")} />
            <Stat label="Evaluator" value={status?.evaluator ? `${status.evaluator.phase} · ${status.evaluator.last_ms} ms` : "—"} />
          </dl>
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-white/10 pt-4">
            <BigButton
              onClick={() => act(sim?.simulated_band ? "Simulated band off" : "Simulated band on", "/api/sim/simulated_band", { enabled: !sim?.simulated_band })}
              icon={WatchIcon}
              pressed={!!sim?.simulated_band}
            >
              Simulated band for R-120
            </BigButton>
            <span className="text-sm text-muted-foreground">Fallback when the Polar band or iPhone isn&apos;t available.</span>
          </div>
        </Panel>

        <NightPanel sim={sim} act={act} />
      </div>

      <InjectorPanel injections={status?.injections ?? []} onDone={(ok, text) => { setMsg({ ok, text }); void refresh(); }} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Reset demo">
          <p className="mb-3 text-base text-muted-foreground">
            Ends every injected scenario, unpauses, turns night mode, the display offset and the simulated band off.
            Recorded history is kept (re-seed with <code>make seed</code>).
          </p>
          <BigButton
            icon={RotateCcw}
            tone="red"
            onClick={() => {
              if (window.confirm("Reset the demo controls and end all injected scenarios?")) {
                void act("Demo reset", "/api/sim/reset");
              }
            }}
          >
            Reset demo
          </BigButton>
        </Panel>
        <HeartbeatPanel />
      </div>
    </main>
  );
}

type Act = (label: string, path: string, body?: unknown) => Promise<void>;

function NightPanel({ sim, act }: { sim: SimState | null; act: Act }) {
  const now = useNow();
  const [time, setTime] = useState("02:14");
  const [err, setErr] = useState("");
  const offset = sim?.display_clock_offset_s ?? 0;
  return (
    <Panel title="Night mode & display clock">
      <div className="flex flex-wrap items-center gap-2">
        <BigButton
          icon={Moon}
          pressed={!!sim?.force_night}
          tone={sim?.force_night ? "indigo" : undefined}
          onClick={() => act(sim?.force_night ? "Night mode off" : "Night mode on", "/api/sim/force_night", { enabled: !sim?.force_night })}
        >
          {sim?.force_night ? "Night mode on" : "Force night"}
        </BigButton>
        <span className="text-base text-muted-foreground">Night rules (22:00–06:00) apply now, e.g. fall-prevention bed exits.</span>
      </div>
      <form
        className="mt-4 flex flex-wrap items-end gap-2 border-t border-white/10 pt-4"
        onSubmit={(e) => {
          e.preventDefault();
          try {
            const s = offsetForDisplayTime(time, Date.now());
            setErr("");
            void act(`Display clock set to ${time}`, "/api/sim/force_night", { enabled: !!sim?.force_night, display_clock_offset_s: s });
          } catch (x) {
            setErr(x instanceof Error ? x.message : String(x));
          }
        }}
      >
        <label className="flex flex-col gap-1 text-sm text-muted-foreground">
          Show the clock as
          <input
            value={time}
            onChange={(e) => setTime(e.target.value)}
            inputMode="numeric"
            placeholder="HH:MM"
            className="h-12 w-28 rounded-lg border border-input bg-background/60 px-3 font-mono text-lg text-foreground"
          />
        </label>
        <BigButton type="submit" icon={Clock}>
          Set clock
        </BigButton>
        {offset !== 0 && (
          <BigButton
            onClick={() => act("Display clock back to real time", "/api/sim/force_night", { enabled: !!sim?.force_night, display_clock_offset_s: 0 })}
          >
            Real time
          </BigButton>
        )}
      </form>
      <p className="mt-2 text-sm text-muted-foreground">
        Displayed {now === null ? "--:--" : formatFacilityClock(now, offset)} · offset {Math.round(offset / 60)} min
        {err && <span className="ml-2 text-red-200">{err}</span>}
      </p>
    </Panel>
  );
}

export function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl bg-card p-4 ring-1 ring-white/10">
      <h2 className="mb-3 text-xl font-semibold">{title}</h2>
      {children}
    </section>
  );
}

const TONES = {
  default: "bg-white/10 hover:bg-white/15",
  teal: "bg-teal-400 text-teal-950 hover:bg-teal-300",
  red: "bg-red-500/20 text-red-100 ring-1 ring-red-400/50 hover:bg-red-500/30",
  indigo: "bg-indigo-500/30 text-indigo-50 ring-1 ring-indigo-300/60",
};

export function BigButton({
  icon: Icon,
  tone = "default",
  pressed,
  children,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  icon?: typeof Play;
  tone?: keyof typeof TONES;
  pressed?: boolean;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      className={cn("flex h-12 items-center gap-2 rounded-xl px-4 text-base font-semibold disabled:opacity-50", TONES[tone])}
      {...rest}
    >
      {Icon && <Icon className="size-5" aria-hidden />}
      {children}
    </button>
  );
}
