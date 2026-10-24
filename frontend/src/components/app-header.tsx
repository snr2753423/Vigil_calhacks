"use client";

import { Moon, Settings, Sun, Volume2, VolumeX } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { cn } from "cn";

import { ConnectionBadge } from "@/components/connection-badge";
import { NavMenu } from "@/components/nav-menu";
import { StaffOnDuty } from "@/components/staff-on-duty";
import { useClock } from "@/hooks/use-clock";
import { useTheme } from "@/hooks/use-theme";
import { FACILITY_SHORT_NAME } from "@/lib/facility";
import { facilityClock12 } from "@/lib/format";
import { useLiveStore } from "@/store/live";
import { ROLES, type Role, usePrefs } from "@/store/prefs";

export function AppHeader() {
  const path = usePathname();
  const { now, offsetS, replay } = useClock();
  const sim = useLiveStore((s) => s.sim);
  const clock = now === null ? "" : facilityClock12(now, offsetS);
  if (path.startsWith("/bed/")) return null; // the iPhone bed-sensor page is a standalone tool
  const clockTitle = replay ? "Replay time (last night)" : sim?.force_night ? "Night mode forced for the demo" : "Facility time";
  const note = replay ? null : [sim?.paused && "paused", sim?.force_night && "night mode"].filter(Boolean).join(" · ");

  return (
    <header className="print-hide border-b border-line bg-background">
      <div className="mx-auto flex max-w-[80rem] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3.5 sm:px-6">
        <Link href="/" className="flex min-h-11 items-baseline gap-2.5">
          <span className="text-[22px] font-bold tracking-tight text-accent">Vigil</span>
          <span className="text-muted-foreground">{FACILITY_SHORT_NAME}</span>
        </Link>
        <NavMenu />
        <div className="ml-auto flex items-center gap-3 sm:gap-[18px]">
          <StaffOnDuty className="hidden text-muted-foreground xl:block" />
          <span className="flex flex-col items-end leading-tight">
            <time className="text-[22px] font-bold whitespace-nowrap" title={clockTitle} suppressHydrationWarning>
              {clock}
            </time>
            {note && <span className="text-sm text-muted-foreground">{note}</span>}
          </span>
          <ConnectionBadge />
          <ThemeToggle />
          <SettingsMenu />
        </div>
      </div>
    </header>
  );
}

const iconBtn =
  "flex size-11 shrink-0 items-center justify-center rounded-[10px] border border-line text-muted-foreground hover:bg-surface-2 hover:text-foreground";

/** Day / Night: follows the facility clock until tapped; remembered on this device. */
function ThemeToggle() {
  const { theme, choice, toggle } = useTheme();
  const next = theme === "night" ? "day" : "night";
  return (
    <button
      type="button"
      onClick={toggle}
      className={iconBtn}
      title={`${theme === "night" ? "Night" : "Day"} colours · ${choice === "auto" ? "follows the facility clock (night 9 PM–7 AM)" : "set on this device"}`}
      aria-label={`Night or day colours: now ${theme}. Switch to ${next}`}
    >
      {theme === "night" ? <Moon className="size-5" aria-hidden /> : <Sun className="size-5" aria-hidden />}
    </button>
  );
}

/** Role and alert sound: per-device settings that don't need to sit in the header row. */
function SettingsMenu() {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const role = usePrefs((s) => s.role);
  const setRole = usePrefs((s) => s.setRole);
  const sound = usePrefs((s) => s.sound);
  const setSound = usePrefs((s) => s.setSound);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        className={iconBtn}
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen(!open)}
        title={`${ROLES[role].label} · sound ${sound ? "on" : "off"}`}
      >
        <Settings className="size-5" aria-hidden />
        <span className="sr-only">Settings: role and alert sound</span>
      </button>
      {open && (
        <div className="absolute right-0 z-50 mt-1 w-72 space-y-3 rounded-[14px] border border-line bg-surface p-4 shadow-[var(--shadow)]">
          <label className="block">
            <span className="mb-1 block text-sm text-muted-foreground">I am the</span>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as Role)}
              className="vg-input"
            >
              {(Object.keys(ROLES) as Role[]).map((r) => (
                <option key={r} value={r}>
                  {ROLES[r].label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => setSound(!sound)}
            aria-pressed={sound}
            className={cn("vg-btn-secondary w-full justify-start")}
          >
            {sound ? <Volume2 className="size-5" aria-hidden /> : <VolumeX className="size-5" aria-hidden />}
            Alert sound {sound ? "on" : "off"}
          </button>
        </div>
      )}
    </div>
  );
}
