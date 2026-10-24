import { cn } from "cn";

import { TIERS } from "@/lib/tiers";
import type { Tier } from "@/lib/types";

type Props = {
  tier: Tier;
  score?: number;
  size?: "sm" | "md" | "lg";
  /** Add the calm action ("High · Check within 15 minutes"). */
  showAction?: boolean;
  /** Replace the word after the tier ("High · #1 to check"). */
  suffix?: string;
  className?: string;
};

const SIZES = {
  sm: "min-h-7 gap-1.5 px-2.5 text-sm [&_svg]:size-3.5",
  md: "min-h-8 gap-1.5 px-3 text-[15px] [&_svg]:size-4",
  lg: "min-h-10 gap-2 px-4 text-base [&_svg]:size-5",
};

/** Tier as icon + word (+ score), never colour alone (SPEC §7d). */
export function TierChip({ tier, score, size = "md", showAction = false, suffix, className }: Props) {
  const t = TIERS[tier];
  const Icon = t.icon;
  return (
    <span className={cn("inline-flex shrink-0 items-center whitespace-nowrap rounded-full font-bold", SIZES[size], t.pill, className)}>
      <Icon aria-hidden strokeWidth={2.6} />
      <span>{t.label}</span>
      {score !== undefined && <span className="font-normal opacity-90">{Math.round(score)}</span>}
      {showAction && <span className="font-normal">· {t.action}</span>}
      {suffix && <span>· {suffix}</span>}
    </span>
  );
}
