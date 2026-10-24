"use client";

import { X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef } from "react";
import { cn } from "cn";

import { TierChip } from "@/components/tier-chip";
import { useNow } from "@/hooks/use-now";
import type { ToastAlert } from "@/lib/live";
import { playCriticalChime } from "@/lib/sound";
import { TIERS } from "@/lib/tiers";
import { useLiveStore } from "@/store/live";
import { usePrefs } from "@/store/prefs";

const HIGH_TTL_MS = 20_000; // HIGH toasts fade on their own; CRITICAL stays until opened or dismissed

/** New HIGH / CRITICAL alerts from /ws/live, with a chime for CRITICAL (SPEC §11.1). */
export function AlertToasts() {
  const alerts = useLiveStore((s) => s.alerts);
  const dismiss = useLiveStore((s) => s.dismiss);
  const sound = usePrefs((s) => s.sound);
  const now = useNow();
  const chimed = useRef(new Set<string>());

  useEffect(() => {
    for (const a of alerts) {
      if (a.sound && !chimed.current.has(a.key)) {
        chimed.current.add(a.key);
        if (sound) playCriticalChime();
      }
    }
  }, [alerts, sound]);

  useEffect(() => {
    if (now === null) return;
    for (const a of alerts) if (a.tier !== "CRITICAL" && now - a.receivedAt > HIGH_TTL_MS) dismiss(a.key);
  }, [now, alerts, dismiss]);

  return (
    <div
      className="pointer-events-none fixed inset-x-0 bottom-0 z-50 flex flex-col items-stretch gap-2 p-3 sm:inset-x-auto sm:right-0 sm:bottom-auto sm:top-20 sm:w-[26rem]"
    >
      {alerts.map((a) => (
        <Toast key={a.key} alert={a} onDismiss={() => dismiss(a.key)} />
      ))}
    </div>
  );
}

function Toast({ alert: a, onDismiss }: { alert: ToastAlert; onDismiss: () => void }) {
  const t = TIERS[a.tier];
  const reason = a.rule?.text ?? a.factors[0] ?? "Priority rose";
  return (
    <div
      role={a.tier === "CRITICAL" ? "alert" : "status"}
      className={cn(
        "pointer-events-auto relative overflow-hidden rounded-xl bg-popover p-4 pl-5 shadow-2xl ring-1 ring-white/15",
        "animate-in fade-in slide-in-from-right-8 duration-300",
      )}
    >
      <span className={cn("absolute inset-y-0 left-0 w-1.5", t.accent)} aria-hidden />
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <TierChip tier={a.tier} score={a.score} size="sm" />
            <span className="text-sm text-muted-foreground">{t.action}</span>
          </div>
          <p className="text-lg font-semibold leading-tight">
            {a.name} <span className="font-normal text-muted-foreground">· Room {a.room}</span>
          </p>
          <p className="text-base leading-snug">{reason}</p>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="-mr-1 -mt-1 flex size-11 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-white/10"
          aria-label={`Dismiss alert for ${a.name}`}
        >
          <X className="size-5" />
        </button>
      </div>
      <Link
        href={`/resident/${a.resident_id}`}
        onClick={onDismiss}
        className="mt-3 flex h-11 items-center justify-center rounded-lg bg-white/10 text-base font-medium hover:bg-white/15"
      >
        Open {a.name.split(" ")[0]}
      </Link>
    </div>
  );
}
