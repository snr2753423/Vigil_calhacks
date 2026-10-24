import { ArrowLeft, Radio } from "lucide-react";
import Link from "next/link";

import { CodeStatusBadge } from "@/components/code-status-badge";
import { TierChip } from "@/components/tier-chip";
import type { ResidentDetail, Tier } from "@/lib/types";

type Props = { r: ResidentDetail; tier: Tier | null; score: number | null; rank: number | null };

export function ResidentHeader({ r, tier, score, rank }: Props) {
  return (
    <header className="space-y-3">
      <Link href="/" className="inline-flex h-11 items-center gap-1.5 rounded-lg pr-3 text-base text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-5" aria-hidden /> Rounds Queue
      </Link>
      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1 space-y-1">
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">{r.name}</h1>
          <p className="text-lg text-muted-foreground">
            Room {r.room} · {r.unit} · {r.age} {r.sex === "F" ? "F" : "M"}
            {r.is_live && (
              <span className="ml-2 inline-flex items-center gap-1 rounded-md bg-teal-500/15 px-2 py-0.5 text-sm font-semibold text-teal-100">
                <Radio className="size-4" aria-hidden /> Live sensors
              </span>
            )}
          </p>
        </div>
        <CodeStatusBadge status={r.code_status} size="lg" />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {tier && <TierChip tier={tier} score={score ?? undefined} size="lg" showAction />}
        {rank !== null && <span className="text-base text-muted-foreground">#{rank} in the queue</span>}
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-2xl bg-card p-4 text-base ring-1 ring-white/10 sm:grid-cols-4">
        <Item label="Mobility" value={r.mobility} />
        <Item label="Morse fall scale" value={`${r.morse}${r.morse >= 45 ? " · high risk" : ""}`} />
        <Item label="Cognition" value={r.cognition} />
        <Item label="Emergency contact" value={r.emergency_contact ? `${r.emergency_contact.name} (${r.emergency_contact.relation})` : "—"} />
        <div className="col-span-2 sm:col-span-4">
          <dt className="text-sm text-muted-foreground">Diagnoses</dt>
          <dd>{r.diagnoses.join(" · ") || "—"}</dd>
        </div>
        {r.medications?.length > 0 && (
          <div className="col-span-2 sm:col-span-4">
            <dt className="text-sm text-muted-foreground">Medications</dt>
            <dd>
              {r.medications.map((m, i) => (
                <span key={m.name}>
                  {i > 0 && " · "}
                  {m.name}
                  {m.flags.length > 0 && (
                    <span className="ml-1 rounded bg-amber-400/15 px-1.5 text-sm text-amber-100">{m.flags.join(", ").replace("_", "-")}</span>
                  )}
                </span>
              ))}
            </dd>
          </div>
        )}
      </dl>
    </header>
  );
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="first-letter:uppercase">{value}</dd>
    </div>
  );
}
