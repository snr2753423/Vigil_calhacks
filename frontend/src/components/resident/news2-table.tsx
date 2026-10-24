import { cn } from "cn";

import type { News2 } from "@/lib/types";

const ROWS: { key: keyof News2["params"]; label: string; unit: string; source: string }[] = [
  { key: "rr", label: "Respiratory rate", unit: "/min", source: "bed sensor" },
  { key: "spo2", label: "SpO₂", unit: "%", source: "wristband" },
  { key: "hr", label: "Heart rate", unit: " bpm", source: "wristband" },
  { key: "sbp", label: "Systolic BP", unit: " mmHg", source: "spot check" },
  { key: "temp", label: "Core temperature", unit: " °C", source: "spot check" },
  { key: "consciousness", label: "Consciousness", unit: "", source: "nurse note" },
];

const POINTS_CLASS = ["", "bg-high-bg text-high", "bg-high-bg text-high", "bg-crit-bg text-crit"];

function fmt(key: string, v: number | boolean, unit: string): string {
  if (key === "consciousness") return v ? "New confusion" : "Alert";
  return `${Number.isInteger(v) ? v : (v as number).toFixed(1)}${unit}`;
}

/** NEWS2 subset with the inputs Vigil has (SPEC §7b); missing inputs score nothing and say so. */
export function News2Table({ n }: { n: News2 }) {
  return (
    <section aria-labelledby="news2-heading" className="space-y-2 vg-card p-5">
      <h2 id="news2-heading" className="flex items-baseline justify-between text-xl font-bold">
        NEWS2
        <span className="text-2xl tabular-nums">
          {n.total}
          {n.forces_high && <span className="ml-2 align-middle text-sm font-sans text-high">at least High</span>}
        </span>
      </h2>
      <p className="text-sm text-muted-foreground">
        Partial score from available inputs · SpO₂ scale {n.spo2_scale}
        {n.spo2_scale === 2 ? " (COPD)" : ""}
      </p>
      <table className="w-full text-base">
        <caption className="sr-only">NEWS2 parameters</caption>
        <thead>
          <tr className="text-left text-sm text-muted-foreground">
            <th className="py-1 font-normal">Parameter</th>
            <th className="py-1 font-normal">Latest</th>
            <th className="py-1 text-right font-normal">Points</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {ROWS.map((r) => {
            const p = n.params[r.key];
            return (
              <tr key={r.key}>
                <th scope="row" className="py-2 text-left font-normal">
                  {r.label}
                  <span className="block text-xs text-muted-foreground">{r.source}</span>
                </th>
                <td className="py-2 tabular-nums">
                  {p ? fmt(r.key, p.value, r.unit) : <span className="font-sans text-muted-foreground">— no reading</span>}
                </td>
                <td className="py-2 text-right">
                  {p ? (
                    <span className={cn("inline-block min-w-8 rounded-md px-2 py-0.5 text-center tabular-nums", POINTS_CLASS[p.points])}>
                      {p.points}
                    </span>
                  ) : (
                    "–"
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
