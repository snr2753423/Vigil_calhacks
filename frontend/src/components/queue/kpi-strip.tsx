import { ClockAlert, WatchIcon } from "lucide-react";
import { cn } from "cn";

import { TIER_ORDER, TIERS } from "@/lib/tiers";
import type { Kpis } from "@/lib/types";

export function KpiStrip({ kpis }: { kpis: Kpis | null }) {
  return (
    <ul className="grid grid-cols-3 gap-2 sm:grid-cols-6" aria-label="Facility summary">
      {TIER_ORDER.map((t) => {
        const meta = TIERS[t];
        const Icon = meta.icon;
        const n = kpis?.tiers[t] ?? 0;
        return (
          <li
            key={t}
            className={cn("flex flex-col justify-between rounded-xl px-3 py-2 ring-1", meta.chip, t === "CRITICAL" && n > 0 && "ring-2")}
          >
            <span className="flex items-center gap-1.5 text-sm font-medium">
              <Icon className="size-4" aria-hidden /> {meta.label}
            </span>
            <span className="font-mono text-3xl font-semibold tabular-nums">{kpis ? n : "–"}</span>
          </li>
        );
      })}
      <KpiTile icon={ClockAlert} label="Checks overdue" value={kpis?.overdue_checks} hint="no check in 2 h" />
      <KpiTile icon={WatchIcon} label="Device issues" value={kpis?.devices_offline} hint="band off / offline" />
    </ul>
  );
}

function KpiTile({ icon: Icon, label, value, hint }: { icon: typeof WatchIcon; label: string; value?: number; hint: string }) {
  return (
    <li className="flex flex-col justify-between rounded-xl bg-card px-3 py-2 ring-1 ring-white/10" title={hint}>
      <span className="flex items-center gap-1.5 text-sm font-medium text-muted-foreground">
        <Icon className="size-4" aria-hidden /> {label}
      </span>
      <span className="font-mono text-3xl font-semibold tabular-nums">{value ?? "–"}</span>
    </li>
  );
}
