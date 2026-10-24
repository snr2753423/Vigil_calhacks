// Hidden demo controls (press "?"): keyboard shortcuts for the presenter. Pure key → action mapping, tested.

export type ShortcutAction =
  | { kind: "toggle_panel" }
  | { kind: "close_panel" }
  | { kind: "play_pause" }
  | { kind: "bookmark"; index: number }
  | { kind: "force_night" }
  | { kind: "bed_exit" }
  | { kind: "bed_return" }
  | { kind: "live" }
  | { kind: "speed" }
  | { kind: "goto"; href: string };

export const SHORTCUTS: { keys: string; label: string }[] = [
  { keys: "?", label: "Show / hide this panel" },
  { keys: "Space or P", label: "Play / pause the replay (starts it from live)" },
  { keys: "1 – 9", label: "Jump to a bookmark (from live: opens the replay there, paused)" },
  { keys: "S", label: "Replay speed 30× → 60× → 120×" },
  { keys: "L", label: "Back to live" },
  { keys: "N", label: "Toggle force night" },
  { keys: "B", label: "Bed exit for R-120 (Thomas Grant)" },
  { keys: "Shift + B", label: "R-120 back in bed" },
  { keys: "G then Q / R / H / A / D / U / M", label: "Go to Queue, Thomas Grant (R-120), Handoff, Ask, Devices, Under the hood, Admin" },
];

export const GOTO: Record<string, string> = {
  q: "/",
  r: "/resident/120",
  h: "/handoff",
  a: "/ask",
  d: "/devices",
  u: "/under-the-hood",
  m: "/admin",
};

export type KeyLike = Pick<KeyboardEvent, "key" | "shiftKey" | "metaKey" | "ctrlKey" | "altKey">;

/** Typing in a form field, or a browser/OS shortcut: never a demo shortcut. */
export function ignoreKey(e: KeyLike, target: EventTarget | null): boolean {
  if (e.metaKey || e.ctrlKey || e.altKey) return true;
  const el = target as HTMLElement | null;
  if (!el || typeof el.tagName !== "string") return false;
  const tag = el.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable === true;
}

/** The action for a key press; `afterG` is true when the previous key was a lone "g". */
export function actionFor(e: KeyLike, afterG = false): ShortcutAction | null {
  const k = e.key;
  if (afterG) {
    const href = GOTO[k.toLowerCase()];
    return href ? { kind: "goto", href } : null;
  }
  if (k === "?") return { kind: "toggle_panel" };
  if (k === "Escape") return { kind: "close_panel" };
  if (k === " " || k === "p" || k === "P") return { kind: "play_pause" };
  if (/^[1-9]$/.test(k)) return { kind: "bookmark", index: Number(k) - 1 };
  if (k === "n" || k === "N") return { kind: "force_night" };
  if (k === "B" || (k === "b" && e.shiftKey)) return { kind: "bed_return" };
  if (k === "b") return { kind: "bed_exit" };
  if (k === "l" || k === "L") return { kind: "live" };
  if (k === "s" || k === "S") return { kind: "speed" };
  return null;
}

/** The next replay speed after `current` (wraps around). */
export function nextSpeed(current: number | undefined, speeds: readonly number[]): number {
  const i = speeds.indexOf(current ?? -1);
  return speeds[(i + 1) % speeds.length];
}
