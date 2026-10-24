import { facilityHHMM } from "@/lib/format";
import type { ComponentKey, Components } from "@/lib/types";

const TREND_LABEL = { rr: "RR", spo2: "SpO₂", hr: "HR" } as const;

// Each bar is a component's points out of its maximum; both come from the backend (SPEC §7d weights).
const PARTS: { key: ComponentKey; label: string; note?: (c: Components) => string }[] = [
  { key: "news", label: "NEWS2 (available inputs)", note: (c) => `NEWS2 ${c.news2_total}` },
  {
    key: "anom",
    label: "Change vs personal normal",
    note: (c) =>
      c.anom_source === "snowflake"
        ? `Snowflake anomaly detection${c.anom_as_of ? ` · known ${facilityHHMM(Date.parse(c.anom_as_of))}` : ""}`
        : "Tiger baseline z-scores (no fresh Snowflake result)",
  },
  {
    key: "trend",
    label: "Forecast trend",
    note: (c) =>
      c.trend_metric && c.trend_crosses_at && c.trend_threshold != null
        ? `Snowflake FORECAST: ${TREND_LABEL[c.trend_metric]} ${c.trend_direction === "up" ? "≥" : "≤"} ${c.trend_threshold} by ~${facilityHHMM(Date.parse(c.trend_crosses_at))}`
        : "Snowflake FORECAST (on HIGH/CRITICAL): no NEWS2 +3 crossing within 4 h",
  },
  { key: "behav", label: "Behavior", note: () => "bed exits · restlessness · stillness" },
  { key: "concern", label: "Staff concern", note: () => "latest note" },
  { key: "overdue", label: "Time since check" },
];

type Props = { c: Components; score: number; ruleText: string | null; news2ForcesHigh: boolean };

export function ScoreBreakdown({ c, score, ruleText, news2ForcesHigh }: Props) {
  const base = PARTS.reduce((s, p) => s + c.points[p.key], 0);
  return (
    <section aria-labelledby="score-heading" className="space-y-3 rounded-2xl bg-card p-4 ring-1 ring-white/10">
      <h2 id="score-heading" className="flex items-baseline justify-between text-xl font-semibold">
        Why this score
        <span className="font-mono text-3xl tabular-nums">{Math.round(score)}</span>
      </h2>
      <ul className="space-y-3">
        {PARTS.map((p) => {
          const max = c.max_points[p.key];
          const pts = c.points[p.key];
          return (
            <li key={p.key}>
              <div className="flex items-baseline justify-between gap-2 text-base">
                <span className="min-w-0">
                  {p.label}
                  {p.note && <span className="block text-sm text-muted-foreground">{p.note(c)}</span>}
                </span>
                <span className="shrink-0 font-mono tabular-nums">
                  {pts.toFixed(1)} <span className="text-sm text-muted-foreground">/ {max}</span>
                </span>
              </div>
              <div
                className="mt-1 h-3 overflow-hidden rounded-full bg-white/[0.06]"
                role="meter"
                aria-valuemin={0}
                aria-valuemax={max}
                aria-valuenow={Math.round(pts * 10) / 10}
                aria-label={p.label}
              >
                <div className="h-full rounded-full bg-teal-300" style={{ width: `${Math.min(100, (pts / max) * 100)}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
      <div className="space-y-1 border-t border-white/10 pt-3 text-base text-muted-foreground">
        <p>
          Weighted sum {base.toFixed(1)}
          {c.multi_signal && ` · +10 multi-signal pattern (${c.anomalous_metrics.join(", ")})`}
        </p>
        {ruleText && <p className="text-red-100">Rule floor applies: {ruleText}</p>}
        {!ruleText && news2ForcesHigh && score > base + (c.multi_signal ? 10 : 0) + 0.5 && (
          <p className="text-orange-100">NEWS2 has a single 3 or totals 5+, so the score is held at High (60).</p>
        )}
      </div>
    </section>
  );
}
