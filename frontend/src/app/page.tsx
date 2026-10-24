import { AlertOctagon, AlertTriangle, CheckCircle2, Eye, ListOrdered } from "lucide-react";

import { HeartbeatPanel } from "@/components/heartbeat-panel";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

// Tier vocabulary from SPEC §7d: always icon + label, never color alone.
const TIERS = [
  { label: "Critical", hint: "go now", icon: AlertOctagon, className: "text-red-300 bg-red-500/10" },
  { label: "High", hint: "within 15 min", icon: AlertTriangle, className: "text-orange-300 bg-orange-500/10" },
  { label: "Watch", hint: "first on next round", icon: Eye, className: "text-amber-200 bg-amber-500/10" },
  { label: "Stable", hint: "routine", icon: CheckCircle2, className: "text-emerald-300 bg-emerald-500/10" },
];

export default function RoundsQueuePage() {
  return (
    <main className="mx-auto w-full max-w-7xl flex-1 space-y-6 px-4 py-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Rounds Queue</h1>
        <p className="text-muted-foreground">Who to check next, and why.</p>
      </div>

      <ul className="flex flex-wrap gap-2" aria-label="Priority tiers">
        {TIERS.map(({ label, hint, icon: Icon, className }) => (
          <li key={label} className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${className}`}>
            <Icon className="size-4" aria-hidden />
            <span className="font-medium">{label}</span>
            <span className="opacity-70">· {hint}</span>
          </li>
        ))}
      </ul>

      <div className="grid gap-6 lg:grid-cols-[1fr_minmax(0,28rem)]">
        <Card className="min-h-64">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <ListOrdered className="size-5" aria-hidden /> Ranked residents
            </CardTitle>
            <CardDescription>
              The ranked queue appears here once the simulator and scoring engine are running.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex h-40 items-center justify-center rounded-lg border border-dashed border-border/70 text-sm text-muted-foreground">
              No residents yet
            </div>
          </CardContent>
        </Card>
        <HeartbeatPanel />
      </div>
    </main>
  );
}
