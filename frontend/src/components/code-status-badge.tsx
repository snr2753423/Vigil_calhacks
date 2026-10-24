import { cn } from "cn";

import type { CodeStatus } from "@/lib/types";

export const CODE_LABEL: Record<CodeStatus, string> = {
  FULL: "Full code",
  DNR: "DNR",
  "DNR-DNI": "DNR · DNI",
  COMFORT: "Comfort care",
};

/** Code status as a quiet outlined tag (the resident page shows it large). */
export function CodeStatusBadge({ status, className }: { status: CodeStatus; className?: string }) {
  return (
    <span
      className={cn("inline-flex shrink-0 items-center whitespace-nowrap rounded-lg border border-line px-2.5 py-1 text-sm text-muted-foreground", className)}
      title="Code status"
    >
      <span className="sr-only">Code status: </span>
      {CODE_LABEL[status] ?? status}
    </span>
  );
}
