import {
  Activity,
  AlertOctagon,
  BedDouble,
  Clock,
  type LucideIcon,
  MessageSquareText,
  TrendingUp,
  WatchIcon,
} from "lucide-react";
import { cn } from "cn";

import type { FactorKind } from "@/lib/types";

const KIND: Record<FactorKind, { icon: LucideIcon; label: string; className: string }> = {
  rule: { icon: AlertOctagon, label: "Rule", className: "bg-red-500/15 text-red-100" },
  vital: { icon: Activity, label: "Vital sign", className: "bg-sky-500/12 text-sky-100" },
  behavior: { icon: BedDouble, label: "Behavior", className: "bg-violet-500/14 text-violet-100" },
  concern: { icon: MessageSquareText, label: "Staff note", className: "bg-fuchsia-500/14 text-fuchsia-100" },
  overdue: { icon: Clock, label: "Check due", className: "bg-slate-500/20 text-slate-100" },
  device: { icon: WatchIcon, label: "Device", className: "bg-slate-500/20 text-slate-200" },
  trend: { icon: TrendingUp, label: "Snowflake forecast", className: "bg-amber-500/14 text-amber-100" },
};

/** One deterministic contributing factor (never AI-invented, SPEC §7d). */
export function FactorChip({ text, kind, className }: { text: string; kind: FactorKind; className?: string }) {
  const k = KIND[kind] ?? KIND.vital;
  const Icon = k.icon;
  return (
    <span className={cn("inline-flex min-h-8 items-center gap-1.5 rounded-lg px-2.5 py-1 text-sm leading-tight", k.className, className)}>
      <Icon className="size-4 shrink-0 opacity-80" aria-label={k.label} />
      <span>{text}</span>
    </span>
  );
}
