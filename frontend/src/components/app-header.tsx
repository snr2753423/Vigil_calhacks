"use client";

import { Moon, Pause, Radio, Rewind, Volume2, VolumeX } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "cn";

import { ConnectionBadge } from "@/components/connection-badge";
import { NavMenu } from "@/components/nav-menu";
import { StaffOnDuty } from "@/components/staff-on-duty";
import { useClock } from "@/hooks/use-clock";
import { FACILITY_SHORT_NAME, formatFacilityClock } from "@/lib/facility";
import { useLiveStore } from "@/store/live";
import { ROLES, type Role, usePrefs } from "@/store/prefs";

export function AppHeader() {
  const path = usePathname();
  const { now, offsetS, replay } = useClock();
  const sim = useLiveStore((s) => s.sim);
  const night = useLiveStore((s) => s.queue?.night ?? false);
  const clock = now === null ? "--:--:--" : formatFacilityClock(now, offsetS);
  if (path.startsWith("/bed/")) return null; // the iPhone bed-sensor page is a standalone tool
  const moon = night || (!replay && !!sim?.force_night);
  const clockTitle = replay ? "Replay time (last night)" : sim?.force_night ? "Night mode forced for the demo" : undefined;

  return (
    <header className="z-40 border-b sm:sticky sm:top-0 border-border/60 bg-background/90 backdrop-blur">
      <div className="mx-auto flex max-w-[96rem] flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
        <Link href="/" className="flex items-baseline gap-2.5">
          <span className="text-2xl font-semibold tracking-tight text-teal-300">Vigil</span>
          <span className="hidden text-sm text-muted-foreground sm:inline">{FACILITY_SHORT_NAME}</span>
        </Link>
        <NavMenu />
        {/* Phones: logo, menu and clock on the first row, the status controls on the second. */}
        <Clock clock={clock} moon={moon} title={clockTitle} className="ml-auto flex sm:hidden" />
        <StaffOnDuty />
        <div className="flex w-full flex-wrap items-center gap-2 sm:ml-auto sm:w-auto sm:justify-end">
          <span
            className={cn(
              "inline-flex h-9 items-center gap-1.5 rounded-full px-3 text-sm font-semibold uppercase ring-1",
              replay ? "bg-violet-500/20 text-violet-100 ring-violet-400/50" : "bg-teal-500/15 text-teal-100 ring-teal-400/40",
            )}
          >
            {replay ? <Rewind className="size-4" aria-hidden /> : <Radio className="size-4" aria-hidden />}
            {replay ? "Replay" : "Live"}
          </span>
          {sim?.paused && (
            <span className="inline-flex h-9 items-center gap-1.5 rounded-full bg-amber-500/15 px-3 text-sm font-medium text-amber-100 ring-1 ring-amber-400/40">
              <Pause className="size-4" aria-hidden /> Paused
            </span>
          )}
          <ConnectionBadge />
          <RoleSwitcher />
          <SoundToggle />
          <Clock clock={clock} moon={moon} title={clockTitle} className="hidden sm:flex" />
        </div>
      </div>
    </header>
  );
}

function Clock({ clock, moon, title, className }: { clock: string; moon: boolean; title?: string; className: string }) {
  return (
    <div className={cn("items-center gap-1.5", className)} title={title}>
      {moon && <Moon className="size-5 text-indigo-200" aria-label="Night" />}
      <time className="text-right font-mono text-xl tabular-nums sm:min-w-[6.2rem] sm:text-2xl" suppressHydrationWarning>
        {clock}
      </time>
    </div>
  );
}

function RoleSwitcher() {
  const role = usePrefs((s) => s.role);
  const setRole = usePrefs((s) => s.setRole);
  return (
    <label className="flex items-center">
      <span className="sr-only">Role</span>
      <select
        value={role}
        onChange={(e) => setRole(e.target.value as Role)}
        className="h-9 rounded-lg border border-input bg-input/30 px-2 text-sm"
      >
        {(Object.keys(ROLES) as Role[]).map((r) => (
          <option key={r} value={r}>
            {ROLES[r].short}
          </option>
        ))}
      </select>
    </label>
  );
}

function SoundToggle() {
  const sound = usePrefs((s) => s.sound);
  const setSound = usePrefs((s) => s.setSound);
  return (
    <button
      type="button"
      onClick={() => setSound(!sound)}
      aria-pressed={sound}
      className="flex size-9 items-center justify-center rounded-lg text-muted-foreground ring-1 ring-input hover:bg-white/5"
      title={sound ? "Alert sound on" : "Alert sound off"}
    >
      {sound ? <Volume2 className="size-5" aria-hidden /> : <VolumeX className="size-5" aria-hidden />}
      <span className="sr-only">{sound ? "Alert sound on" : "Alert sound off"}</span>
    </button>
  );
}
