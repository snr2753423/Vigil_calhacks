"use client";

import { Activity } from "lucide-react";
import { useShallow } from "zustand/react/shallow";

import { HeartbeatChart } from "@/components/charts/heartbeat-chart";
import { Stat } from "@/components/stat";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useNow } from "@/hooks/use-now";
import { useLiveStore } from "@/store/live";

export function HeartbeatPanel() {
  const now = useNow();
  const { lastHeartbeat: hb, intervalsMs, heartbeatIntervalS, status } = useLiveStore(
    useShallow((s) => ({
      lastHeartbeat: s.lastHeartbeat,
      intervalsMs: s.intervalsMs,
      heartbeatIntervalS: s.heartbeatIntervalS,
      status: s.status,
    })),
  );
  const ageS = hb && now !== null ? Math.max(0, (now - hb.receivedAt) / 1000) : null;
  const fresh = status === "open" && ageS !== null && ageS < (heartbeatIntervalS ?? 2) * 2;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-lg">
          <span className="relative flex size-3" aria-hidden>
            {fresh && (
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-teal-400 opacity-75" />
            )}
            <span className={`relative inline-flex size-3 rounded-full ${fresh ? "bg-teal-400" : "bg-zinc-600"}`} />
          </span>
          Live link · /ws/live
        </CardTitle>
        <CardDescription>
          {fresh
            ? `Backend heartbeat every ${heartbeatIntervalS ?? 2} s through Caddy.`
            : "Waiting for the backend heartbeat…"}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
          <Stat mono label="Heartbeat #" value={hb ? String(hb.seq) : "—"} />
          <Stat mono label="Last seen" value={ageS === null ? "—" : `${ageS.toFixed(0)} s ago`} />
          <Stat mono label="Server time (UTC)" value={hb ? hb.ts.slice(11, 19) : "—"} />
          <Stat mono label="Viewers" value={hb ? String(hb.clients) : "—"} />
        </dl>
        <div>
          <p className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
            <Activity className="size-3.5" aria-hidden /> Gap between heartbeats (ECharts)
          </p>
          <HeartbeatChart intervalsMs={intervalsMs} expectedMs={(heartbeatIntervalS ?? 2) * 1000} />
        </div>
      </CardContent>
    </Card>
  );
}
