import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import { Avatar } from "@/components/avatar";
import { CODE_LABEL } from "@/components/code-status-badge";
import { TierChip } from "@/components/tier-chip";
import type { ResidentDetail, Tier } from "@/lib/types";

type Props = { r: ResidentDetail; tier: Tier | null; rank: number | null };

const COGNITION: Record<ResidentDetail["cognition"], string | null> = {
  intact: null,
  mild: "mild memory loss",
  moderate: "moderate dementia",
  severe: "severe dementia",
};

/** "needs 1 person to help her walk" */
export function mobilityWords(m: ResidentDetail["mobility"], her: string): string {
  return {
    independent: `walks on ${her} own`,
    "1-assist": `needs 1 person to help ${her === "his" ? "him" : "her"} walk`,
    "2-assist": `needs 2 people to help ${her === "his" ? "him" : "her"} move`,
    bedbound: "stays in bed",
  }[m];
}

export function ResidentHeader({ r, tier, rank }: Props) {
  const her = r.sex === "M" ? "his" : "her";
  const sub = [
    String(r.age),
    `Room ${r.room}, ${r.unit}`,
    COGNITION[r.cognition],
    mobilityWords(r.mobility, her),
  ].filter(Boolean);
  const contact = r.emergency_contact;
  return (
    <>
      <Link href="/" className="mb-2 inline-flex min-h-11 items-center gap-1.5 text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-5" aria-hidden /> Rounds queue
      </Link>
      <header className="vg-card flex flex-wrap items-center gap-4 p-5 sm:p-6">
        <Avatar name={r.name} size="lg" />
        <div className="min-w-0 flex-1">
          <h1 className="text-[28px] leading-tight font-bold sm:text-[32px]">{r.name}</h1>
          <p className="text-muted-foreground">{sub.join(" · ")}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {tier && (
              <TierChip
                tier={tier}
                suffix={tier === "STABLE" ? "resting as usual" : rank !== null ? `#${rank} to check` : undefined}
              />
            )}
            {r.is_live && <span className="rounded-full bg-surface-2 px-3 py-0.5 text-sm text-muted-foreground">Live sensors</span>}
          </div>
        </div>
        <div className="w-full text-left sm:ml-auto sm:w-auto sm:text-right">
          <p className="text-[13px] tracking-[0.08em] text-soft uppercase">Code status</p>
          <p className="text-[22px] font-bold">{CODE_LABEL[r.code_status]}</p>
          {contact && (
            <p className="text-sm text-muted-foreground">
              {contact.relation.charAt(0).toUpperCase() + contact.relation.slice(1)}: {contact.name}
              {contact.phone && <span className="block">{contact.phone}</span>}
            </p>
          )}
        </div>
      </header>
    </>
  );
}
