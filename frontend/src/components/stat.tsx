import { cn } from "cn";

/** A small label/value pair inside a <dl>. */
export function Stat({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className={cn("text-muted-foreground", mono ? "text-xs uppercase tracking-wide" : "text-sm")}>{label}</dt>
      <dd className={mono ? "text-xl whitespace-nowrap tabular-nums" : undefined}>{value}</dd>
    </div>
  );
}
