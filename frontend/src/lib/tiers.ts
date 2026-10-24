// Tier vocabulary (SPEC §7d, Phase 11): every tier has an icon + a word + a calm action phrase, never colour alone.
import { AlertOctagon, AlertTriangle, CheckCircle2, Eye, type LucideIcon } from "lucide-react";

import type { Tier } from "@/lib/types";

export type TierMeta = {
  label: string;
  /** What to do, in calm words: "Go now", "Check within 15 minutes", … */
  action: string;
  icon: LucideIcon;
  /** Pill: tinted background + AA text colour. */
  pill: string;
  /** Floor-plan room. */
  room: string;
  /** Left edge of the "Check next" card and toasts. */
  edge: string;
  /** Text colour. */
  text: string;
  /** Hex for charts / canvas: [night, day]. */
  hex: string;
  dayHex: string;
};

export const TIERS: Record<Tier, TierMeta> = {
  CRITICAL: {
    label: "Critical",
    action: "Go now",
    icon: AlertOctagon,
    pill: "bg-crit-bg text-crit",
    room: "bg-crit-bg text-crit font-bold outline-2 outline-crit-solid",
    edge: "border-l-crit-solid",
    text: "text-crit",
    hex: "#e8836c",
    dayHex: "#b4432f",
  },
  HIGH: {
    label: "High",
    action: "Check within 15 minutes",
    icon: AlertTriangle,
    pill: "bg-high-bg text-high",
    room: "bg-high-bg text-high font-bold outline-2 outline-high-solid",
    edge: "border-l-high-solid",
    text: "text-high",
    hex: "#e3a556",
    dayHex: "#b36b12",
  },
  WATCH: {
    label: "Watch",
    action: "Keep an eye on",
    icon: Eye,
    pill: "bg-watch-bg text-watch",
    room: "bg-watch-bg text-watch font-bold",
    edge: "border-l-watch-solid",
    text: "text-watch",
    hex: "#cdbb6a",
    dayHex: "#7a6a1c",
  },
  STABLE: {
    label: "Stable",
    action: "Resting as usual",
    icon: CheckCircle2,
    pill: "bg-stable-bg text-stable",
    room: "bg-surface-2 text-soft",
    edge: "border-l-stable-solid",
    text: "text-stable",
    hex: "#9aa59f",
    dayHex: "#5d6b66",
  },
};

export const TIER_ORDER: Tier[] = ["CRITICAL", "HIGH", "WATCH", "STABLE"];
