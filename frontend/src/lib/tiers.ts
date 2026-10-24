// Tier vocabulary (SPEC §7d): every tier has an icon + label + color, never color alone.
import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  Eye,
  type LucideIcon,
} from "lucide-react";

import type { Tier } from "@/lib/types";

export type TierMeta = {
  label: string;
  action: string;
  icon: LucideIcon;
  /** Chip: text + tinted background + ring. */
  chip: string;
  /** Solid swatch for the floor grid and score bars. */
  solid: string;
  /** Left accent bar on queue cards. */
  accent: string;
  /** Hex for charts / canvas. */
  hex: string;
};

export const TIERS: Record<Tier, TierMeta> = {
  CRITICAL: {
    label: "Critical",
    action: "Go now",
    icon: AlertOctagon,
    chip: "bg-red-500/15 text-red-200 ring-red-400/50",
    solid: "bg-red-500 text-red-950",
    accent: "bg-red-500",
    hex: "#ef4444",
  },
  HIGH: {
    label: "High",
    action: "Within 15 min",
    icon: AlertTriangle,
    chip: "bg-orange-500/15 text-orange-200 ring-orange-400/50",
    solid: "bg-orange-400 text-orange-950",
    accent: "bg-orange-400",
    hex: "#fb923c",
  },
  WATCH: {
    label: "Watch",
    action: "First on next round",
    icon: Eye,
    chip: "bg-amber-400/10 text-amber-100 ring-amber-300/40",
    solid: "bg-amber-300 text-amber-950",
    accent: "bg-amber-300",
    hex: "#fcd34d",
  },
  STABLE: {
    label: "Stable",
    action: "Routine",
    icon: CheckCircle2,
    chip: "bg-emerald-500/10 text-emerald-200 ring-emerald-400/30",
    solid: "bg-slate-700 text-slate-200",
    accent: "bg-emerald-500/40",
    hex: "#34d399",
  },
};

export const TIER_ORDER: Tier[] = ["CRITICAL", "HIGH", "WATCH", "STABLE"];
