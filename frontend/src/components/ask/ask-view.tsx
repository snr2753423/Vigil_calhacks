"use client";

import ReactEChartsCore from "echarts-for-react/esm/core";
import { BadgeCheck, Code2, Loader2, MessageCircleQuestion, Send, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cn } from "cn";

import { askChartOption, formatCell, prettyColumn } from "@/lib/ask";
import { getJSON, postJSON } from "@/lib/api";
import { facilityHHMM } from "@/lib/format";
import { echarts } from "@/lib/echarts";
import type { AskResult } from "@/lib/types";

type Turn = { id: number; question: string; result: AskResult | null; error: string | null };

/** Ask the facility (SPEC §9 ⑧, §11.4): a question → Snowflake Cortex Analyst (REST) → SQL → table + chart. */
export function AskView() {
  const [suggested, setSuggested] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const nextId = useRef(1);
  const bottom = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    getJSON<{ suggested: string[] }>("/api/ask")
      .then((r) => setSuggested(r.suggested))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const ask = async (q: string) => {
    const text = q.trim();
    if (text.length < 3 || busy) return;
    const id = nextId.current++;
    const history = turns
      .filter((t) => t.result)
      .flatMap((t) => [
        { role: "user", content: t.question },
        { role: "analyst", content: t.result!.interpretation || t.result!.sql || "" },
      ])
      .filter((m) => m.content);
    setTurns((ts) => [...ts, { id, question: text, result: null, error: null }]);
    setQuestion("");
    setBusy(true);
    try {
      const result = await postJSON<AskResult>("/api/ask", { question: text, history });
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, result } : t)));
    } catch (e) {
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, error: e instanceof Error ? e.message : "Failed" } : t)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto flex w-full max-w-[72rem] flex-1 flex-col gap-4 px-3 py-4 sm:px-4">
      <header className="space-y-1">
        <h1 className="flex items-center gap-2 text-2xl font-semibold">
          <MessageCircleQuestion className="size-6" aria-hidden /> Ask the facility
        </h1>
        <p className="text-base text-muted-foreground">
          Questions in plain words, answered from the facility&apos;s data in Snowflake. Cortex Analyst writes the SQL
          from a semantic model of VIGIL.CORE; Vigil runs it and charts the result.
        </p>
      </header>

      <div className="flex flex-wrap gap-2" aria-label="Suggested questions">
        {suggested.map((s) => (
          <button
            key={s}
            type="button"
            disabled={busy}
            onClick={() => ask(s)}
            className="min-h-11 rounded-full bg-white/5 px-4 py-2 text-left text-base ring-1 ring-white/10 hover:bg-white/10 disabled:opacity-50"
          >
            {s}
          </button>
        ))}
      </div>

      <div className="space-y-4">
        {turns.map((t) => (
          <article key={t.id} className="space-y-3">
            <p className="ml-auto w-fit max-w-[90%] rounded-2xl bg-teal-500/15 px-4 py-2.5 text-base text-teal-50 ring-1 ring-teal-400/30">
              {t.question}
            </p>
            {t.error ? (
              <p className="rounded-xl bg-red-500/15 px-4 py-3 text-base text-red-100" role="alert">
                {t.error}
              </p>
            ) : t.result ? (
              <Answer r={t.result} onSuggest={ask} />
            ) : (
              <p className="flex items-center gap-2 text-base text-muted-foreground" role="status">
                <Loader2 className="size-5 animate-spin" aria-hidden /> Cortex Analyst is reading the question…
              </p>
            )}
          </article>
        ))}
        <div ref={bottom} />
      </div>

      <form
        className="sticky bottom-3 mt-auto flex gap-2 rounded-2xl bg-card p-2 ring-1 ring-white/15"
        onSubmit={(e) => {
          e.preventDefault();
          void ask(question);
        }}
      >
        <label htmlFor="ask-input" className="sr-only">
          Your question
        </label>
        <input
          id="ask-input"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          maxLength={500}
          placeholder="e.g. How many bed exits per unit last night?"
          className="h-12 min-w-0 flex-1 rounded-xl bg-background/60 px-4 text-base"
        />
        <button
          type="submit"
          disabled={busy || question.trim().length < 3}
          className="flex h-12 items-center gap-2 rounded-xl bg-teal-500/20 px-5 text-base font-semibold text-teal-50 ring-1 ring-teal-400/50 hover:bg-teal-500/30 disabled:opacity-50"
        >
          {busy ? <Loader2 className="size-5 animate-spin" aria-hidden /> : <Send className="size-5" aria-hidden />}
          Ask
        </button>
      </form>
    </main>
  );
}

function Answer({ r, onSuggest }: { r: AskResult; onSuggest: (q: string) => void }) {
  const chart = askChartOption(r);
  const interpretation = r.interpretation.replace(/^This is our interpretation of your question:\s*/i, "").trim();
  return (
    <section className="space-y-3 rounded-2xl bg-card p-4 ring-1 ring-white/10">
      {r.fallback && (
        <p className="rounded-lg bg-amber-500/10 p-3 text-base text-amber-100 ring-1 ring-amber-400/30" role="status">
          Snowflake Cortex Analyst is unreachable right now, so this is the last answer it gave to this question
          {r.fallback.answered_at ? ` (${facilityHHMM(Date.parse(r.fallback.answered_at))})` : ""}.
        </p>
      )}
      {interpretation && (
        <p className="text-base">
          <span className="text-sm text-muted-foreground">Interpreted as: </span>
          {interpretation}
        </p>
      )}
      {r.verified_query && (
        <p className="flex items-center gap-1.5 text-sm text-emerald-200">
          <BadgeCheck className="size-4" aria-hidden /> Answered with the verified query “{r.verified_query.question}”
        </p>
      )}
      {r.suggestions.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">Did you mean:</p>
          <div className="flex flex-wrap gap-2">
            {r.suggestions.map((s) => (
              <button key={s} type="button" onClick={() => onSuggest(s)} className="min-h-10 rounded-full bg-white/5 px-3 py-1.5 text-left text-base ring-1 ring-white/10 hover:bg-white/10">
                {s}
              </button>
            ))}
          </div>
        </div>
      )}
      {r.error && (
        <p className="rounded-lg bg-red-500/10 p-3 text-base text-red-100" role="alert">
          {r.error}
        </p>
      )}
      {r.sql && !r.error && (
        <>
          {chart && (
            <figure className="space-y-1">
              {chart.series.length > 0 && (
                <figcaption className="flex flex-wrap gap-3 px-1">
                  {chart.series.map((s) => (
                    <span key={s.name} className="flex items-center gap-1.5 text-sm text-muted-foreground">
                      <span aria-hidden className="inline-block size-3 rounded-sm" style={{ background: s.color }} />
                      {s.name}
                    </span>
                  ))}
                </figcaption>
              )}
              <ReactEChartsCore echarts={echarts} option={chart.option} notMerge style={{ height: chart.height, width: "100%" }} />
            </figure>
          )}
          <ResultTable r={r} />
        </>
      )}
      {r.sql && (
        <details className="rounded-xl bg-white/[0.03] p-3 ring-1 ring-white/10">
          <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium text-muted-foreground">
            <Code2 className="size-4" aria-hidden /> SQL from Cortex Analyst
          </summary>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap font-mono text-sm text-slate-200">{r.sql}</pre>
        </details>
      )}
      <p className="flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
        <Sparkles className="size-4" aria-hidden /> Snowflake Cortex Analyst REST · {(r.analyst_ms / 1000).toFixed(1)} s
        {r.exec_ms !== undefined && ` · SQL API ${(r.exec_ms / 1000).toFixed(1)} s`}
        {r.models.length > 0 && ` · ${r.models.join(", ")}`}
      </p>
    </section>
  );
}

function ResultTable({ r }: { r: AskResult }) {
  if (!r.columns.length) return null;
  if (!r.rows.length) {
    return <p className="rounded-lg bg-white/[0.03] p-3 text-base text-muted-foreground">No rows: nothing matched in the data.</p>;
  }
  const numeric = (t: string) => t === "fixed" || t === "real";
  return (
    <div className="max-h-[28rem] overflow-auto rounded-xl ring-1 ring-white/10">
      <table className="w-full border-collapse text-left text-base">
        <thead className="sticky top-0 bg-card text-sm text-muted-foreground">
          <tr>
            {r.columns.map((c) => (
              <th key={c.name} className={cn("border-b border-white/10 px-3 py-2 font-medium", numeric(c.type) && "text-right")}>
                {prettyColumn(c.name)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {r.rows.map((row, i) => (
            <tr key={i} className="border-b border-white/5">
              {row.map((v, j) => (
                <td key={j} className={cn("px-3 py-1.5", numeric(r.columns[j].type) && "text-right font-mono tabular-nums")}>
                  {formatCell(v, r.columns[j].type)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="px-3 py-1.5 text-sm text-muted-foreground">
        {r.row_count} row{r.row_count === 1 ? "" : "s"}
        {r.truncated ? " (first 500 shown)" : ""}
      </p>
    </div>
  );
}
