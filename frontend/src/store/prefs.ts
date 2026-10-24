import { create } from "zustand";

import type { ThemeChoice } from "@/lib/theme";

// Per-device conveniences (role, sound, day/night override), kept in localStorage when it is available.

export type Role = "cna" | "rn" | "don";

export const ROLES: Record<Role, { label: string; short: string; noteRole: "CNA" | "LPN" | "RN" }> = {
  cna: { label: "CNA / LPN (night shift)", short: "CNA / LPN", noteRole: "CNA" },
  rn: { label: "Charge Nurse (RN)", short: "Charge RN", noteRole: "RN" },
  don: { label: "Director of Nursing", short: "DON", noteRole: "RN" },
};

type Prefs = { role: Role; sound: boolean; theme: ThemeChoice };

export const PREFS_KEY = "vigil.prefs";
const DEFAULTS: Prefs = { role: "cna", sound: true, theme: "auto" };
const THEMES: ThemeChoice[] = ["auto", "day", "night"];

function load(): Prefs {
  try {
    const raw = window.localStorage.getItem(PREFS_KEY);
    if (!raw) return DEFAULTS;
    const p = JSON.parse(raw) as Partial<Prefs>;
    return {
      role: p.role && p.role in ROLES ? p.role : DEFAULTS.role,
      sound: p.sound ?? DEFAULTS.sound,
      theme: p.theme && THEMES.includes(p.theme) ? p.theme : DEFAULTS.theme,
    };
  } catch {
    return DEFAULTS;
  }
}

function save(p: Prefs): void {
  try {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify(p));
  } catch {
    // private mode / blocked storage: keep it in memory only
  }
}

type PrefsStore = Prefs & {
  hydrated: boolean;
  hydrate: () => void;
  setRole: (role: Role) => void;
  setSound: (sound: boolean) => void;
  setTheme: (theme: ThemeChoice) => void;
};

export const usePrefs = create<PrefsStore>()((set, get) => {
  const update = (patch: Partial<Prefs>) => {
    set(patch);
    const { role, sound, theme } = get();
    save({ role, sound, theme });
  };
  return {
    ...DEFAULTS,
    hydrated: false,
    hydrate: () => set({ ...load(), hydrated: true }),
    setRole: (role) => update({ role }),
    setSound: (sound) => update({ sound }),
    setTheme: (theme) => update({ theme }),
  };
});
