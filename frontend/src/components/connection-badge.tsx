"use client";

import { Loader2, Wifi, WifiOff } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { ConnectionStatus } from "@/lib/live";
import { useLiveStore } from "@/store/live";

const STATUS: Record<ConnectionStatus, { label: string; icon: typeof Wifi; className: string }> = {
  open: { label: "Connected", icon: Wifi, className: "bg-emerald-500/15 text-emerald-300" },
  connecting: { label: "Connecting", icon: Loader2, className: "bg-amber-500/15 text-amber-300" },
  closed: { label: "Reconnecting", icon: WifiOff, className: "bg-red-500/15 text-red-300" },
};

export function ConnectionBadge() {
  const status = useLiveStore((s) => s.status);
  const { label, icon: Icon, className } = STATUS[status];
  return (
    <Badge className={`h-7 gap-1.5 px-3 text-sm ${className}`} role="status" aria-live="polite">
      <Icon className={status === "connecting" ? "animate-spin" : undefined} aria-hidden />
      {label}
    </Badge>
  );
}
