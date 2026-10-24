// Day / night theme (Phase 11): follows the facility clock (night 21:00–07:00), forced night and replays of last
// night, unless this device picked one by hand.
import { FACILITY_TZ } from "@/lib/facility";

export type Theme = "day" | "night";
export type ThemeChoice = "auto" | Theme;

export const NIGHT_FROM_H = 21;
export const NIGHT_TO_H = 7;

export function clockTheme(hour: number, forceNight = false, replay = false): Theme {
  return forceNight || replay || hour >= NIGHT_FROM_H || hour < NIGHT_TO_H ? "night" : "day";
}

export function effectiveTheme(choice: ThemeChoice, auto: Theme): Theme {
  return choice === "auto" ? auto : choice;
}

/** The header toggle: flip what is shown; picking what the clock would pick anyway goes back to "auto". */
export function toggleTheme(choice: ThemeChoice, auto: Theme): ThemeChoice {
  const next: Theme = effectiveTheme(choice, auto) === "night" ? "day" : "night";
  return next === auto ? "auto" : next;
}

/** Runs before first paint (inline in <head>): the stored choice, else the facility clock. Self-contained. */
export const THEME_BOOT_SCRIPT = `(()=>{try{var t="auto";try{t=(JSON.parse(localStorage.getItem("vigil.prefs")||"{}").theme)||"auto"}catch(e){}
if(t!=="day"&&t!=="night"){var h=Number(new Intl.DateTimeFormat("en-US",{timeZone:"${FACILITY_TZ}",hour:"numeric",hourCycle:"h23"}).format(new Date()))%24;
t=(h>=${NIGHT_FROM_H}||h<${NIGHT_TO_H})?"night":"day"}document.documentElement.dataset.theme=t}catch(e){}})();`;
