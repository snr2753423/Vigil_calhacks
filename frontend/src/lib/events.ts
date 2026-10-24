// Plain-language lines for the resident's events timeline.
import type { ResidentEvent } from "@/lib/types";

export type EventView = { title: string; detail: string | null; tone: "alert" | "staff" | "info" | "good" };

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" ? v : null);

export function describeEvent(e: ResidentEvent): EventView {
  const p = e.payload ?? {};
  switch (e.type) {
    case "check_logged":
      return {
        title: p.round ? `Round check (${str(p.round)})` : `Check logged${p.kind && p.kind !== "bedside" ? ` · ${str(p.kind)}` : ""}`,
        detail: str(p.note),
        tone: "good",
      };
    case "note_added":
      return {
        title: `${p.source === "voice" ? "Voice note" : "Note"}${p.author_role ? ` · ${str(p.author_role)}` : ""}`,
        detail: str(p.text),
        tone: "staff",
      };
    case "bed_exit":
      return { title: "Got out of bed", detail: null, tone: "info" };
    case "bed_return":
      return { title: "Back in bed", detail: null, tone: "info" };
    case "fall_suspected":
      return { title: "Suspected fall", detail: str(p.text), tone: "alert" };
    case "alert_raised": {
      const factors = Array.isArray(p.factors) ? (p.factors as string[]).join(" · ") : null;
      return { title: `Alert: ${String(p.tier ?? "").toLowerCase()} (${Math.round(num(p.score) ?? 0)})`, detail: factors, tone: "alert" };
    }
    case "alert_resolved":
      return {
        title: `Alert cleared${p.reason === "checked" ? " after check" : ""}`,
        detail: p.tier ? `now ${String(p.tier).toLowerCase()}` : null,
        tone: "good",
      };
    case "intake_logged": {
      const bits = [
        str(p.meal) ? `${str(p.meal)} ${num(p.meal_pct) ?? "?"}%` : null,
        num(p.fluid_ml) !== null ? `${num(p.fluid_ml)} ml fluids` : null,
      ].filter(Boolean);
      return { title: "Intake", detail: bits.join(" · ") || null, tone: "info" };
    }
    case "spot_check": {
      const bits = [
        num(p.sbp) !== null ? `BP ${num(p.sbp)}${num(p.dbp) !== null ? `/${num(p.dbp)}` : ""}` : null,
        num(p.temp_c) !== null ? `Temp ${num(p.temp_c)} °C` : null,
      ].filter(Boolean);
      return { title: "Spot check", detail: bits.join(" · ") || null, tone: "info" };
    }
    case "device_offline":
      return { title: "Device offline", detail: null, tone: "info" };
    case "device_online":
      return { title: "Device back online", detail: null, tone: "info" };
    default:
      return { title: String(e.type).replace(/_/g, " "), detail: null, tone: "info" };
  }
}
