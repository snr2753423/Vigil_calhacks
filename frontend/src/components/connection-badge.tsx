"use client";

import { Loader2, WifiOff } from "lucide-react";

import { useLiveStore } from "@/store/live";

/** Silent while connected (screen readers only); words only when the live feed is down. */
export function ConnectionBadge() {
  const status = useLiveStore((s) => s.status);
  if (status === "open") {
    return (
      <span className="sr-only" role="status" aria-live="polite">
        Connected
      </span>
    );
  }
  const connecting = status === "connecting";
  return (
    <span
      className="inline-flex min-h-8 items-center gap-1.5 rounded-full bg-high-bg px-3 text-sm font-bold text-high"
      role="status"
      aria-live="polite"
    >
      {connecting ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <WifiOff className="size-4" aria-hidden />}
      {connecting ? "Connecting" : "Reconnecting"}
    </span>
  );
}
