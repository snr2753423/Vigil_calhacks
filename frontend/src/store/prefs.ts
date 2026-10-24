import { create } from "zustand";

// Per-device conveniences (role, sound), kept in localStorage when it is available.

export type Role = "cna" | "rn" | "don";

export const ROLES: Record<Role, { label: string; short: string; noteRole: "CNA" | "LPN" | "RN" }> = {
  cna: { label: "CNA / LPN (night shift)", short: "CNA / LPN", noteRole: "CNA" },
  rn: { label: "Charge Nurse (RN)", short: "Charge RN", noteRole: "RN" },
  don: { label: "Director of Nursing", short: "DON", noteRole: "RN" },
};

type Prefs = { role: Role; sound: boolean };

const KEY = "vigil.prefs";
const DEFAULTS: Prefs = { role: "cna", sound: true };

function load(): Prefs {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    const p = JSON.parse(raw) as Partial<Prefs>;
    return { role: p.role && p.role in ROLES ? p.role : DEFAULTS.role, sound: p.sound ?? DEFAULTS.sound };
  } catch {
    return DEFAULTS;
  }
}

function save(p: Prefs): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    // private mode / blocked storage: keep it in memory only
  }
}

type PrefsStore = Prefs & {
  hydrated: boolean;
  hydrate: () => void;
  setRole: (role: Role) => void;
  setSound: (sound: boolean) => void;
};

export const usePrefs = create<PrefsStore>()((set, get) => ({
  ...DEFAULTS,
  hydrated: false,
  hydrate: () => set({ ...load(), hydrated: true }),
  setRole: (role) => {
    set({ role });
    save({ role, sound: get().sound });
  },
  setSound: (sound) => {
    set({ sound });
    save({ role: get().role, sound });
  },
}));
