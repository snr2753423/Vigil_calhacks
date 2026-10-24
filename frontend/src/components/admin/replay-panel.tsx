"use client";

import { AlertTriangle, CheckCircle2, Loader2, Play, RefreshCw } from "lucide-react";
import { useState } from "react";
import { cn } from "cn";

import { BigButton, Panel } from "@/components/admin/admin-panel";
import { BookmarkButtons, PlaybackButtons, replayCall, SPEEDS } from "@/components/replay/replay-controls";
import { Stat } from "@/components/stat";
import { useClock } from "@/hooks/use-clock";
import { useReplayStatus } from "@/hooks/use-replay-status";
import { postJSON } from "@/lib/api";
import { facilityDayHHMM, facilityHHMM } from "@/lib/format";
import type { PrepareProgress, ReplayStatus } from "@/lib/types";

type Msg = { ok: boolean; text: string } | null;

const PHASES: Record<PrepareProgress["phase"], string> = {
  idle: "Idle",
  detecting: "1/5 Snowflake anomaly detection",
  notes: "2/5 Snowflake AI_EXTRACT on the night's notes",
  scoring: "3/5 Scoring the night through the live pipeline",
  forecasting: "4/5 Snowflake FORECAST where live would ask",
  assessing: "5/5 Snowflake Cortex AI cards",
  done: "Done",
  error: "Failed",
};

/** Replay (SPEC §4, §11.8): Prepare replay, then play last night from any time, at 30/60/120×, with bookmarks. */
export function ReplayPanel() {
  const { status, error, refresh } = useReplayStatus();
  const [msg, setMsg] = useState<Msg>(null);
  const [from, setFrom] = useState("01:45");
  const [speed, setSpeed] = useState<number>(60);
  const [forceDetect, setForceDetect] = useState(false);
  const [forceCards, setForceCards] = useState(false);
  const [forceDeep, setForceDeep] = useState(false);
  const { now } = useClock();
  const report = (r: { ok: boolean; text: string }) => {
    setMsg(r);
    void refresh();
  };

  const prepared = status?.prepared ?? null;
  const prep = status?.prepare;
  const running = prep?.running ?? false;

  const startPrepare = async () => {
    try {
      await postJSON("/api/replay/prepare", { force_detect: forceDetect, force_cards: forceCards, force_deep: forceDeep });
      report({ ok: true, text: "Prepare replay started: this takes several minutes (detection, notes, scoring, forecasts, AI cards)." });
    } catch (e) {
      report({ ok: false, text: `Prepare failed to start: ${e instanceof Error ? e.message : e}` });
    }
  };

  const play = async () => {
    const r = await replayCall("start", { time: from, speed, play: true });
    report(r.error ? { ok: false, text: r.error } : { ok: true, text: `Replaying from ${from} at ${speed}×` });
  };

  return (
    <Panel title="Replay last night">
      {msg && (
        <p role="status" className={cn("mb-3 rounded-xl px-4 py-3 text-base", msg.ok ? "bg-stable-bg text-stable" : "bg-crit-bg text-crit")}>
          {msg.text}
        </p>
      )}
      {error && !status && <p className="text-base text-crit">{error}</p>}
      {status && (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-3">
            <h3 className="text-lg font-bold">1 · Prepare</h3>
            <p className="text-base text-muted-foreground">
              Precomputes everything the replay reveals, so it never waits on Snowflake: anomaly detection over the
              night, AI_EXTRACT on its nurse notes, the gated scoring pass, forecasts where live would ask for them,
              and Snowflake Cortex cards at each bookmark and escalation.
            </p>
            {prepared ? (
              <PreparedSummary status={status} />
            ) : (
              !running && (
                <p className="flex items-start gap-2 rounded-xl bg-high-bg px-4 py-3 text-base text-high ring-1 ring-high-solid">
                  <AlertTriangle className="mt-0.5 size-5 shrink-0" aria-hidden />
                  Run Prepare replay first: the replay needs its cached Snowflake results and AI cards.
                </p>
              )
            )}
            {prep && (running || prep.phase === "error") && <Progress p={prep} />}
            <div className="flex flex-wrap items-center gap-3">
              <BigButton icon={running ? Loader2 : RefreshCw} tone={prepared ? "default" : "teal"} disabled={running} onClick={startPrepare}>
                {running ? "Preparing…" : prepared ? "Prepare again" : "Prepare replay"}
              </BigButton>
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <input type="checkbox" checked={forceDetect} onChange={(e) => setForceDetect(e.target.checked)} className="size-5" />
                re-run anomaly detection
              </label>
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <input type="checkbox" checked={forceCards} onChange={(e) => setForceCards(e.target.checked)} className="size-5" />
                regenerate AI cards
              </label>
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <input type="checkbox" checked={forceDeep} onChange={(e) => setForceDeep(e.target.checked)} className="size-5" />
                redo forecasts and note extraction
              </label>
            </div>
          </div>

          <div className="space-y-3">
            <h3 className="text-lg font-bold">2 · Play</h3>
            {status.night && (
              <p className="text-base text-muted-foreground">
                Night of {status.night.date}: {facilityDayHHMM(Date.parse(status.night.start))} →{" "}
                {facilityDayHHMM(Date.parse(status.night.end))}, scored every {status.step_s / 60} min of replay time.
              </p>
            )}
            {status.active ? (
              <>
                <p className="text-base">
                  <span className="font-bold text-foreground">Replaying</span> · at{" "}
                  <span className="tabular-nums">{now === null ? "--:--" : facilityHHMM(now)}</span>
                  {status.at_end && " · end of the night"}
                </p>
                <PlaybackButtons onResult={report} />
              </>
            ) : (
              <form
                className="flex flex-wrap items-end gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  void play();
                }}
              >
                <label className="flex flex-col gap-1 text-sm text-muted-foreground">
                  From
                  <input
                    value={from}
                    onChange={(e) => setFrom(e.target.value)}
                    inputMode="numeric"
                    placeholder="HH:MM"
                    className="h-12 w-28 rounded-lg border border-input bg-background/60 px-3 text-lg text-foreground"
                  />
                </label>
                <label className="flex flex-col gap-1 text-sm text-muted-foreground">
                  Speed
                  <select
                    value={speed}
                    onChange={(e) => setSpeed(Number(e.target.value))}
                    className="h-12 rounded-lg border border-input bg-background/60 px-3 text-lg text-foreground"
                  >
                    {SPEEDS.map((s) => (
                      <option key={s} value={s}>
                        {s}×
                      </option>
                    ))}
                  </select>
                </label>
                <BigButton type="submit" icon={Play} tone="teal" disabled={!prepared || running}>
                  Play replay
                </BigButton>
              </form>
            )}
            <h4 className="pt-1 text-base font-bold">Bookmarks</h4>
            {prepared ? (
              <BookmarkButtons bookmarks={status.bookmarks} cursor={status.active && status.cursor ? Date.parse(status.cursor) : null} onResult={report} />
            ) : (
              <p className="text-sm text-muted-foreground">Available once the night is prepared.</p>
            )}
            {status.active && status.stats.steps > 0 && (
              <p className="text-sm text-muted-foreground">
                Each step: the live evaluator scores 120 residents as of the cursor in {status.stats.avg_ms} ms avg (max{" "}
                {status.stats.max_ms} ms){status.stats.behind > 0 && `; ${status.stats.behind} steps ran slower than the speed`}.
                {status.stats.last_error && ` Last error: ${status.stats.last_error}`}
              </p>
            )}
          </div>
        </div>
      )}
    </Panel>
  );
}

function PreparedSummary({ status }: { status: ReplayStatus }) {
  const p = status.prepared!;
  const d = p.detection;
  const a = p.assessments_summary;
  return (
    <div className="space-y-2 rounded-xl bg-surface-2 p-3 border border-line">
      <p className="flex items-center gap-2 text-base text-stable">
        <CheckCircle2 className="size-5" aria-hidden /> Prepared {facilityDayHHMM(Date.parse(p.prepared_at))} in{" "}
        {Math.round(p.seconds)} s
      </p>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-base sm:grid-cols-4">
        <Stat label="Anomaly detection" value={d.ran ? `${(d.results ?? 0).toLocaleString()} results` : "already done"} />
        <Stat label="Scored steps" value={`${p.gated.steps} · ${p.gated.avg_ms} ms`} />
        <Stat label="Vigil alerts" value={String(p.gated.alerts)} />
        <Stat label="AI cards" value={`${a.cortex + a.cached} of ${a.planned}${a.failed ? ` · ${a.failed} failed` : ""}`} />
      </dl>
      <p className="text-sm text-muted-foreground">
        {d.ran ? `ANOMALY_DETECTION ran in ${Math.round(d.seconds ?? 0)} s. ` : `${d.reason}. `}
        Forecasts: {p.forecasts.requested} asked for, {p.forecasts.stored + (p.forecasts.cached ?? 0)} made
        {p.forecasts.failed ? ` (${p.forecasts.failed} without enough data)` : ""}.
        {p.notes ? ` Notes: ${p.notes.extracted + p.notes.cached} of ${p.notes.notes} read by AI_EXTRACT.` : ""}{" "}
        {status.cards} Cortex cards on the night are cached in Tiger.
      </p>
    </div>
  );
}

function Progress({ p }: { p: PrepareProgress }) {
  const pct = p.total ? Math.round((p.step / p.total) * 100) : null;
  return (
    <div className="space-y-1.5 rounded-xl bg-surface-2 p-3 border border-line" role="status">
      <p className={cn("flex items-center gap-2 text-base", p.phase === "error" ? "text-crit" : "text-foreground")}>
        {p.phase !== "error" && <Loader2 className="size-4 animate-spin" aria-hidden />} {PHASES[p.phase]}
        {pct !== null && p.phase !== "error" && <span className="text-sm text-muted-foreground tabular-nums">{p.step}/{p.total}</span>}
      </p>
      {pct !== null && p.phase !== "error" && (
        <div className="h-2 overflow-hidden rounded-full bg-surface-2">
          <div className="h-full bg-accent transition-[width]" style={{ width: `${pct}%` }} />
        </div>
      )}
      <p className="text-sm text-muted-foreground">{p.error ?? p.message}</p>
    </div>
  );
}
