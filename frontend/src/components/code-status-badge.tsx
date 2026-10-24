import { HeartPulse, ShieldAlert, ShieldOff, Flower2 } from "lucide-react";
import { cn } from "cn";

import type { CodeStatus } from "@/lib/types";

const CODE = {
  FULL: { label: "Full code", icon: HeartPulse, className: "bg-sky-500/15 text-sky-100 ring-sky-400/50" },
  DNR: { label: "DNR", icon: ShieldAlert, className: "bg-violet-500/20 text-violet-100 ring-violet-400/60" },
  "DNR-DNI": { label: "DNR · DNI", icon: ShieldOff, className: "bg-violet-500/20 text-violet-100 ring-violet-400/60" },
  COMFORT: { label: "Comfort care", icon: Flower2, className: "bg-rose-500/15 text-rose-100 ring-rose-400/50" },
} satisfies Record<CodeStatus, unknown>;

export function CodeStatusBadge({ status, size = "sm" }: { status: CodeStatus; size?: "sm" | "lg" }) {
  const c = CODE[status] ?? CODE.FULL;
  const Icon = c.icon;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-lg font-semibold uppercase tracking-wide ring-1",
        size === "lg" ? "h-14 gap-2 px-4 text-xl [&_svg]:size-7" : "h-7 gap-1 px-2 text-xs [&_svg]:size-4",
        c.className,
      )}
      title="Code status"
    >
      <Icon aria-hidden />
      <span className="sr-only">Code status: </span>
      {c.label}
    </span>
  );
}
