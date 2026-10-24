import { cn } from "cn";

import { initials } from "@/lib/format";

const SIZES = { sm: "size-[42px] text-[15px]", md: "size-14 text-xl", lg: "size-[72px] text-[26px]" };

/** Initials in a warm circle: residents are people, not rows. */
export function Avatar({ name, size = "md", className }: { name: string; size?: keyof typeof SIZES; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("grid shrink-0 place-items-center rounded-full bg-surface-2 font-bold text-foreground", SIZES[size], className)}
    >
      {initials(name)}
    </span>
  );
}
