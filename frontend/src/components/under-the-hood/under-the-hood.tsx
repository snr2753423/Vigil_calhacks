"use client";

import { Database, Gauge, Loader2, RefreshCw, Snowflake, Timer } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { cn } from "cn";

import { ArchitectureDiagram } from "@/components/under-the-hood/architecture-diagram";
import { getJSON, postJSON } from "@/lib/api";
import { facilityDayHHMM, facilityHHMM, formatMinutes } from "@/lib/format";
import type { BenchResult, OutcomeStats, SnowflakeStats, TigerStats } from "@/lib/types";
import { useLiveStore } from "@/store/live";

const NAMES: Record<number, string> = {
  17: "Rosa Alvarez", 42: "Henry Lee", 101: "Arthur Patel", 110: "James Ortiz", 88: "Margaret Kim", 91: "Elena Rossi",
  33: "Doris Wright", 120: "Thomas Grant",
};

function usePoll<T>(path: string, ms: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setData(await getJSON<T>(path));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load");
    }
  }, [path]);
  useEffect(() => {
    const first = setTimeout(load, 0);
    const t = ms > 0 ? setInterval(load, ms) : undefined;
    return () => {
      clearTimeout(first);
      clearInterval(t);
    };
  }, [load, ms]);
  return { data, error, reload: load };
}

const n = (v: number | null | undefined) => (v === null || v === undefined ? "—" : Math.round(v).toLocaleString());
function bytes(b: number | null | undefined): string {
  if (!b) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1024)));
  return `${(b / 1024 ** i).toFixed(i >= 3 ? 2 : 0)} ${u[i]}`;
}
const ms = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : v >= 10_000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v).toLocaleString()} ms`;

/** /under-the-hood (SPEC §13): proof that each sponsor does work only it can do. Every number is a live query. */
export function UnderTheHood() {
  return (
    <main className="mx-auto w-full max-w-[96rem] flex-1 space-y-4 px-3 py-4 sm:px-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Under the hood</h1>
        <p className="text-base text-muted-foreground">
          Two speeds: Tiger Data keeps the live picture in seconds, Snowflake judges drift against each resident&apos;s
          own normal and explains it. Every number below is read from the databases now.
        </p>
      </div>
      <Section title="Architecture" icon={Gauge}>
        <ArchitectureDiagram />
      </Section>
      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 xl:grid-cols-2">
        <TigerPanel />
        <SnowflakePanel />
      </div>
      <OutcomePanel />
    </main>
  );
}

function Section({ title, icon: Icon, children, aside }: {
  title: string;
  icon: typeof Gauge;
  children: React.ReactNode;
  aside?: React.ReactNode;
}) {
  return (
    <section className="min-w-0 space-y-3 rounded-2xl bg-card p-4 ring-1 ring-white/10">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-xl font-semibold">
          <Icon className="size-5" aria-hidden /> {title}
        </h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl bg-white/[0.04] p-3 ring-1 ring-white/10">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="font-mono text-2xl tabular-nums">{value}</dd>
      {sub && <dd className="text-sm text-muted-foreground">{sub}</dd>}
    </div>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>;
}

// --------------------------------------------------------------------------- Tiger

function TigerPanel() {
  const { data: t, error } = usePoll<TigerStats>("/api/stats/tiger", 5000);
  const c = t?.compression;
  return (
    <Section
      title="Tiger Data · live operational layer"
      icon={Database}
      aside={t?.ok && <span className="text-sm text-muted-foreground">TimescaleDB {t.versions.timescaledb} · toolkit {t.versions.timescaledb_toolkit} · {t.query_ms} ms</span>}
    >
      {!t ? (
        <Loading error={error} />
      ) : !t.ok ? (
        <p className="text-base text-red-200">Tiger unavailable: {t.error}</p>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Tile label="Live ingest" value={`${t.ingest.rows_per_s} rows/s`} sub={`${n(t.ingest.rows_last_60s)} rows in the last minute`} />
            <Tile label="Vitals rows" value={n(t.rows?.vitals)} sub="approximate_row_count" />
            <Tile label="Compression" value={c?.ratio ? `${c.ratio}×` : "—"} sub={`${bytes(c?.before_bytes)} → ${bytes(c?.after_bytes)}`} />
            <Tile label="Chunks" value={`${c?.compressed_chunks ?? "—"} / ${c?.chunks ?? "—"}`} sub="compressed / total (vitals)" />
          </dl>
          <Note>
            Ingest by source:{" "}
            {t.ingest.by_source.length
              ? t.ingest.by_source.map((s) => `${s.source} ${n(s.rows)}`).join(" · ")
              : "nothing in the last minute (simulator paused or stopped)"}
            . Events {n(t.rows?.events)} · priority snapshots {n(t.rows?.priority_snapshots)} · vitals_1m {n(t.rows?.vitals_1m)} ·
            vitals_15m {n(t.rows?.vitals_15m)} · vitals_1h {n(t.rows?.vitals_1h)}.
          </Note>
          <Bench />
          <details className="rounded-xl bg-white/[0.03] p-3 ring-1 ring-white/10">
            <summary className="cursor-pointer text-base font-semibold">Continuous aggregates and jobs</summary>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-[34rem] text-left text-sm">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Job</th>
                    <th className="py-1 pr-3 font-medium">On</th>
                    <th className="py-1 pr-3 font-medium">Every</th>
                    <th className="py-1 pr-3 font-medium">Last run</th>
                    <th className="py-1 pr-3 font-medium">Runs / failures</th>
                  </tr>
                </thead>
                <tbody>
                  {t.jobs.map((j) => (
                    <tr key={j.job_id} className="border-t border-white/5">
                      <td className="py-1 pr-3">{j.proc_name.replace("policy_", "").replaceAll("_", " ")}</td>
                      <td className="py-1 pr-3 font-mono">{j.hypertable_name ?? "—"}</td>
                      <td className="py-1 pr-3 font-mono">{j.schedule}</td>
                      <td className="py-1 pr-3">
                        {j.last_run_status ?? "—"}
                        {j.last_successful_finish && ` · ${facilityHHMM(Date.parse(j.last_successful_finish))}`}
                        {j.last_run_ms !== null && ` · ${ms(j.last_run_ms)}`}
                      </td>
                      <td className="py-1 pr-3 font-mono">{j.total_runs ?? "—"} / {j.total_failures ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Note>
              Real-time aggregation: {t.caggs.map((g) => `${g.view_name} ${g.materialized_only ? "off" : "on"}`).join(" · ")}.
              Hyperfunctions in use: time_bucket_gapfill + locf, percentile_agg / approx_percentile / rollup, stats_agg,
              heartbeat_agg, state_agg, add_job.
            </Note>
          </details>
        </>
      )}
    </Section>
  );
}

function Bench() {
  const [res, setRes] = useState<BenchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      setRes(await postJSON<BenchResult>("/api/bench/hourly_hr"));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const raw = res?.results.raw;
  const agg = res?.results.vitals_1h;
  const max = Math.max(raw?.ms ?? 0, agg?.ms ?? 0, 1);
  return (
    <div className="space-y-2 rounded-xl bg-white/[0.03] p-3 ring-1 ring-white/10">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-base font-semibold">
          <Timer className="size-4" aria-hidden /> Benchmark · 7-day hourly HR for all 120 residents
        </h3>
        <button
          type="button"
          onClick={run}
          disabled={busy}
          className="flex h-11 items-center gap-2 rounded-xl bg-teal-400 px-4 text-base font-semibold text-teal-950 hover:bg-teal-300 disabled:opacity-60"
        >
          {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Gauge className="size-4" aria-hidden />}
          {busy ? "Running…" : res ? "Run again" : "Run benchmark"}
        </button>
      </div>
      {err && <p className="text-sm text-red-200">{err}</p>}
      {res && raw && agg ? (
        <>
          <BenchBar label="Raw vitals rows" sub={`time_bucket over ${"7 d"} of 5-s readings`} msV={raw.ms} max={max} tone="bg-slate-400" />
          <BenchBar label="vitals_1h continuous aggregate" sub="hierarchical cagg, real-time on" msV={agg.ms} max={max} tone="bg-teal-300" />
          <p className="text-base">
            <span className="font-semibold">{res.speedup}× faster</span> from the continuous aggregate · both return{" "}
            {n(agg.rows)} resident-hours (raw {n(raw.rows)}) · mean HR {agg.mean_hr?.toFixed(1)} vs {raw.mean_hr?.toFixed(1)} bpm.
          </p>
          <details>
            <summary className="cursor-pointer text-sm text-muted-foreground">SQL</summary>
            <pre className="mt-1 overflow-x-auto rounded-lg bg-black/30 p-2 text-xs">{Object.entries(res.sql).map(([k, v]) => `-- ${k}\n${v.replace(/\s+/g, " ").trim()}`).join("\n\n")}</pre>
          </details>
        </>
      ) : (
        !busy && <Note>Runs both queries now, back to back on one connection (each wrapped in one outer aggregate, so the time is the database&apos;s work).</Note>
      )}
    </div>
  );
}

function BenchBar({ label, sub, msV, max, tone }: { label: string; sub: string; msV: number; max: number; tone: string }) {
  return (
    <div title={`${label}: ${ms(msV)}`}>
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span>
          {label} <span className="text-muted-foreground">· {sub}</span>
        </span>
        <span className="font-mono text-base tabular-nums">{ms(msV)}</span>
      </div>
      <div className="mt-1 h-3 rounded-r bg-white/[0.04]">
        <div className={cn("h-3 rounded-r", tone)} style={{ width: `${Math.max(0.5, (msV / max) * 100)}%` }} />
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- Snowflake

function SnowflakePanel() {
  const { data: s, error, reload } = usePoll<SnowflakeStats>("/api/stats/snowflake", 15000);
  const [refreshing, setRefreshing] = useState(false);
  const inv = s?.inventory;
  const counts = inv?.counts ?? {};
  const det = s?.last_detection;
  const synced = s?.sync_state ?? {};
  const svc = inv?.search_services?.[0];
  return (
    <Section
      title="Snowflake · intelligence layer"
      icon={Snowflake}
      aside={
        <button
          type="button"
          disabled={refreshing}
          onClick={async () => {
            setRefreshing(true);
            try {
              await getJSON("/api/stats/snowflake?refresh=true");
              await reload();
            } finally {
              setRefreshing(false);
            }
          }}
          className="flex h-10 items-center gap-1.5 rounded-lg px-3 text-sm text-muted-foreground ring-1 ring-white/10 hover:bg-white/5"
        >
          <RefreshCw className={cn("size-4", refreshing && "animate-spin")} aria-hidden /> Re-read Snowflake
        </button>
      }
    >
      {!s ? (
        <Loading error={error} />
      ) : !s.configured ? (
        <p className="text-base">Snowflake is not configured in .env; the fast tier runs alone.</p>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Tile
              label="Anomaly models"
              value={`${inv?.models?.length ?? "—"} × ${counts.anomaly_series ?? "—"}`}
              sub="models × resident series"
            />
            <Tile
              label="Last detection"
              value={det ? ms(det.last_seconds * 1000) : "—"}
              sub={det ? `${n(det.last_rows)} results · ${det.last_run ? facilityDayHHMM(Date.parse(det.last_run)) : ""}` : "none yet"}
            />
            <Tile
              label="Rows synced"
              value={n(synced.vitals_15m?.rows_synced)}
              sub={`vitals_15m buckets · ${n(synced.events?.rows_synced)} events`}
            />
            <Tile
              label="Care-plan chunks"
              value={n(counts.care_plan_chunks as number)}
              sub={`${counts.care_plan_residents ?? "—"} residents · ${svc ? `search ${String(svc.serving_state ?? "").toLowerCase()}` : "Cortex Search"}`}
            />
          </dl>
          <Note>
            In Snowflake now: {n(counts.anomaly_rows as number)} anomaly results · {n(counts.vitals_15m_rows as number)}{" "}
            VITALS_15M rows · {n(counts.assessments as number)} AI assessments · {n(counts.care_plan_docs as number)} parsed
            care plans · model <span className="font-mono">{s.model}</span>.
            {inv?.ms !== undefined && ` Read via the SQL REST API in ${inv.ms} ms${inv.cached ? " (cached)" : ""}.`}
            {inv?.errors?.length ? ` Errors: ${inv.errors.join("; ")}` : ""}
          </Note>

          <h3 className="pt-1 text-base font-semibold">
            Snowflake API calls{" "}
            <span className="font-normal text-muted-foreground">
              · {n(s.cumulative?.calls)} total{s.cumulative?.since ? ` since ${facilityDayHHMM(Date.parse(s.cumulative.since))}` : ""} ·{" "}
              {n(s.totals.calls)} by this backend process
            </span>
          </h3>
          <div className="space-y-2">
            {(s.surfaces ?? []).map((g) => (
              <details key={g.key} className="rounded-xl bg-white/[0.03] p-3 ring-1 ring-white/10" open={g.key !== "connector"}>
                <summary className="flex cursor-pointer flex-wrap items-baseline justify-between gap-2">
                  <span>
                    <span className="font-semibold">{g.label}</span>{" "}
                    <span className="font-mono text-xs text-muted-foreground">{g.endpoint}</span>
                  </span>
                  <span className="font-mono tabular-nums">
                    {n(g.calls)} calls{g.errors ? ` · ${g.errors} errors` : ""}
                  </span>
                </summary>
                {g.features.length === 0 ? (
                  <Note>{g.key === "analyst" ? "Wired for Phase 9 (Ask the facility); no calls yet." : "No calls yet."}</Note>
                ) : (
                  <div className="mt-2 overflow-x-auto">
                    <table className="w-full min-w-[30rem] text-left text-sm">
                      <thead className="text-muted-foreground">
                        <tr>
                          <th className="py-1 pr-3 font-medium">Feature</th>
                          <th className="py-1 pr-3 text-right font-medium">Calls</th>
                          <th className="py-1 pr-3 text-right font-medium">Avg</th>
                          <th className="py-1 font-medium">What it does</th>
                        </tr>
                      </thead>
                      <tbody>
                        {g.features.map((f) => (
                          <tr key={f.feature} className="border-t border-white/5 align-top">
                            <td className="py-1 pr-3 font-mono text-xs">{f.feature}</td>
                            <td className="py-1 pr-3 text-right font-mono tabular-nums">
                              {n(f.calls)}
                              {f.errors ? <span className="text-amber-200"> ({f.errors} err)</span> : null}
                            </td>
                            <td className="py-1 pr-3 text-right font-mono tabular-nums">{ms(f.avg_ms)}</td>
                            <td className="py-1 text-muted-foreground">{f.what}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </details>
            ))}
          </div>
          {s.rest.inference_endpoint.startsWith("blocked") && (
            <Note>
              The Cortex inference REST endpoint is refused for this trial account (403), so each card&apos;s Claude call
              runs SNOWFLAKE.CORTEX.COMPLETE through the SQL REST API instead; the endpoint is retried every 30 min.
            </Note>
          )}
        </>
      )}
    </Section>
  );
}

// --------------------------------------------------------------------------- outcome

function OutcomePanel() {
  const { data: o, error } = usePoll<OutcomeStats>("/api/stats/outcome", 0);
  const mode = useLiveStore((s) => s.sim?.mode);
  return (
    <Section title="Outcome · last night through the pipeline" icon={Gauge}>
      {!o ? (
        <Loading error={error} />
      ) : !o.prepared ? (
        <p className="text-base">{o.message ?? o.error} {mode !== "replay" && "(Admin → Prepare replay.)"}</p>
      ) : (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
          <div className="space-y-3">
            <h3 className="text-base font-semibold">Alarms on the night (22:00–06:30)</h3>
            <dl className="grid grid-cols-2 gap-2">
              <Tile label="Fixed-threshold alarms" value={n(o.fixed!.total)} sub={`${n(o.fixed!.residents)} residents · ${n(o.fixed!.vital_signs)} vital signs, ${n(o.fixed!.bed_exits)} bed exits`} />
              <Tile label="Vigil alerts (HIGH/CRITICAL)" value={n(o.vigil!.total)} sub={`${o.vigil!.residents} residents · ${o.vigil!.rule_alerts} from hard rules`} />
            </dl>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Fixed threshold</th>
                    <th className="py-1 text-right font-medium">Alarms</th>
                  </tr>
                </thead>
                <tbody>
                  {o.fixed!.by_kind.map((k) => (
                    <tr key={k.kind} className="border-t border-white/5">
                      <td className="py-1 pr-3">{k.label}</td>
                      <td className="py-1 text-right font-mono tabular-nums">{n(k.alarms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Scripted resident</th>
                    <th className="py-1 pr-3 text-right font-medium">Fixed alarms</th>
                    <th className="py-1 text-right font-medium">Vigil alerts</th>
                  </tr>
                </thead>
                <tbody>
                  {o.cast!.map((c) => (
                    <tr key={c.resident_id} className="border-t border-white/5">
                      <td className="py-1 pr-3">{NAMES[c.resident_id] ?? `R-${c.resident_id}`}</td>
                      <td className="py-1 pr-3 text-right font-mono tabular-nums">{c.fixed}</td>
                      <td className="py-1 text-right font-mono tabular-nums">{c.vigil}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Note>
              Fixed thresholds as a conventional monitor would set them: {o.rules!.fixed.join(", ")}. Abnormal readings
              less than {o.rules!.merge_minutes} min apart count as one alarm (a conservative count). Vigil alerts are
              what the real evaluator and alert gate raised, as of every 2-min replay step. Computed in {o.query_ms} ms.
            </Note>
          </div>
          <AlvarezLead o={o} />
        </div>
      )}
    </Section>
  );
}

function AlvarezLead({ o }: { o: OutcomeStats }) {
  const a = o.alvarez!;
  const start = Date.parse(o.night!.start);
  const end = Date.parse(o.night!.end);
  const pos = (ts: string) => ((Date.parse(ts) - start) / (end - start)) * 100;
  const marks = [
    a.vigil_watch && { ts: a.vigil_watch, label: "Vigil WATCH", tone: "bg-amber-300" },
    a.vigil_high && { ts: a.vigil_high, label: "Vigil HIGH", tone: "bg-orange-400" },
    a.scheduled_round && { ts: a.scheduled_round, label: "Scheduled round", tone: "bg-slate-300" },
    a.round_check && { ts: a.round_check, label: "Round reaches her", tone: "bg-slate-400" },
    a.first_fixed_vital && { ts: a.first_fixed_vital, label: `First fixed vital alarm (${a.first_fixed_vital_kind})`, tone: "bg-red-400" },
  ].filter(Boolean) as { ts: string; label: string; tone: string }[];
  return (
    <div className="space-y-3">
      <h3 className="text-base font-semibold">Rosa Alvarez · silent infection drift</h3>
      <dl className="grid grid-cols-2 gap-2">
        <Tile
          label="Before her scheduled round"
          value={a.lead_vs_round_min === null ? "—" : formatMinutes(a.lead_vs_round_min)}
          sub={`Vigil HIGH ${a.vigil_high ? facilityHHMM(Date.parse(a.vigil_high)) : "—"} · round ${a.scheduled_round ? facilityHHMM(Date.parse(a.scheduled_round)) : "—"}`}
        />
        <Tile
          label="Before the first fixed vital alarm"
          value={a.lead_vs_fixed_min === null ? "—" : formatMinutes(a.lead_vs_fixed_min)}
          sub={a.first_fixed_vital ? `${a.first_fixed_vital_kind} at ${facilityHHMM(Date.parse(a.first_fixed_vital))}` : "none on the night"}
        />
      </dl>
      <ol className="relative mt-2 space-y-1.5 border-l border-white/15 pl-4" aria-label="Alvarez timeline">
        {marks
          .sort((x, y) => Date.parse(x.ts) - Date.parse(y.ts))
          .map((m) => (
            <li key={m.label} className="relative text-sm">
              <span className={cn("absolute -left-[1.3rem] top-1.5 size-2.5 rounded-full ring-2 ring-card", m.tone)} aria-hidden />
              <span className="font-mono tabular-nums">{facilityHHMM(Date.parse(m.ts))}</span> {m.label}
            </li>
          ))}
      </ol>
      <div className="relative h-6 rounded bg-white/[0.04]" aria-hidden>
        {marks.map((m) => (
          <span key={m.label} title={`${facilityHHMM(Date.parse(m.ts))} ${m.label}`} className={cn("absolute top-1 h-4 w-1.5 rounded", m.tone)} style={{ left: `${pos(m.ts)}%` }} />
        ))}
        <span className="absolute -bottom-5 left-0 text-xs text-muted-foreground">22:00</span>
        <span className="absolute -bottom-5 right-0 text-xs text-muted-foreground">06:30</span>
      </div>
      <div className="pt-4">
        <Note>
          Vigil alerts for her:{" "}
          {a.alerts.map((x) => `${facilityHHMM(Date.parse(x.ts))} ${x.tier} ${x.score} (#${x.rank})`).join(" · ") || "none"}.
          Fixed thresholds: {a.fixed_alarms} alarms, {a.fixed_bed_exit_alarms} of them her assisted toileting bed exits.
        </Note>
        {a.notes.map((x) => (
          <p key={x.ts} className="mt-1 text-sm">
            <span className="font-mono">{facilityHHMM(Date.parse(x.ts))}</span> CNA note: &ldquo;{x.text}&rdquo;
          </p>
        ))}
      </div>
    </div>
  );
}

function Loading({ error }: { error: string | null }) {
  return error ? (
    <p className="text-base text-red-200">{error}</p>
  ) : (
    <p className="flex items-center gap-2 text-base text-muted-foreground">
      <Loader2 className="size-5 animate-spin" aria-hidden /> Querying…
    </p>
  );
}
