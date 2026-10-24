import { cn } from "cn";

import { TIERS } from "@/lib/tiers";
import type { Tier } from "@/lib/types";

type Props = { tier: Tier; score?: number; size?: "sm" | "md" | "lg"; showAction?: boolean; className?: string };

const SIZES = {
  sm: "h-7 gap-1 px-2 text-sm [&_svg]:size-4",
  md: "h-9 gap-1.5 px-3 text-base [&_svg]:size-5",
  lg: "h-11 gap-2 px-4 text-lg [&_svg]:size-6",
};

/** Tier as icon + label (+ score), never color alone (SPEC §7d). */
export function TierChip({ tier, score, size = "md", showAction = false, className }: Props) {
  const t = TIERS[tier];
  const Icon = t.icon;
  return (
    <span
      className={cn("inline-flex shrink-0 items-center rounded-full font-semibold ring-1", SIZES[size], t.chip, className)}
    >
      <Icon aria-hidden />
      <span>{t.label}</span>
      {score !== undefined && <span className="font-mono tabular-nums opacity-90">{Math.round(score)}</span>}
      {showAction && <span className="font-normal opacity-80">· {t.action}</span>}
    </span>
  );
}
