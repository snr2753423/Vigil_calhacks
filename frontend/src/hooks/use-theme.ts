"use client";

import { useClock } from "@/hooks/use-clock";
import { facilityHour } from "@/lib/format";
import { clockTheme, effectiveTheme, type Theme, type ThemeChoice, toggleTheme } from "@/lib/theme";
import { useLiveStore } from "@/store/live";
import { usePrefs } from "@/store/prefs";

export type ThemeState = { theme: Theme; auto: Theme; choice: ThemeChoice; toggle: () => void };

/** The theme the facility clock asks for (display offset, force-night, replay) and this device's choice. */
export function useTheme(): ThemeState {
  const { now, offsetS, replay } = useClock();
  const forceNight = useLiveStore((s) => !!s.sim?.force_night && s.sim.mode !== "replay");
  const choice = usePrefs((s) => s.theme);
  const setTheme = usePrefs((s) => s.setTheme);
  const auto = now === null ? "night" : clockTheme(facilityHour(now + offsetS * 1000), forceNight, replay);
  return { theme: effectiveTheme(choice, auto), auto, choice, toggle: () => setTheme(toggleTheme(choice, auto)) };
}
