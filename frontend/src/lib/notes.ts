// Nurse-note helpers: the recorder's audio format and the AI_EXTRACT chips (SPEC §9 ⑥).
import type { NoteExtract } from "@/lib/types";

/** Recording formats AI_TRANSCRIBE accepts, in order of preference: Chrome records webm/opus, iPhone Safari mp4/AAC. */
export const RECORDER_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"] as const;

export function pickRecorderType(isSupported: (t: string) => boolean): string | null {
  return RECORDER_TYPES.find((t) => isSupported(t)) ?? null;
}

/** File extension for the upload (the backend also reads the MIME type). */
export function audioExtension(mime: string): string {
  const base = mime.split(";")[0].trim().toLowerCase();
  if (base.includes("webm")) return "webm";
  if (base.includes("mp4") || base.includes("m4a") || base.includes("aac")) return "m4a";
  if (base.includes("ogg")) return "ogg";
  if (base.includes("wav")) return "wav";
  return "webm";
}

export type Chip = { label: string; tone: "alert" | "warn" | "info" };

/** The extracted fields worth showing, most important first. "unknown"/none fields are left out. */
export function extractChips(x: NoteExtract): Chip[] {
  const chips: Chip[] = [];
  if (x.confusion_change === "new" || x.confusion_change === "worse") {
    chips.push({ label: `Confusion: ${x.confusion_change} → NEWS2 consciousness +3`, tone: "alert" });
  } else if (x.confusion_change !== "unknown") {
    chips.push({ label: `Confusion: ${x.confusion_change}`, tone: "info" });
  }
  if (x.fall_or_near_fall) chips.push({ label: "Fall or near-fall", tone: "alert" });
  if (x.breathing_concern) chips.push({ label: "Breathing concern", tone: "alert" });
  if (x.fever_or_warm) chips.push({ label: "Warm / fever", tone: "warn" });
  if (x.oral_intake !== "unknown") {
    chips.push({ label: `Intake: ${x.oral_intake}`, tone: x.oral_intake === "normal" ? "info" : "warn" });
  }
  if (x.pain_0_10 !== null && x.pain_0_10 !== undefined) {
    chips.push({ label: `Pain ${x.pain_0_10}/10`, tone: x.pain_0_10 >= 5 ? "warn" : "info" });
  }
  if (x.urinary_symptoms) chips.push({ label: `Urinary: ${x.urinary_symptoms}`, tone: "warn" });
  if (x.mood_behavior) chips.push({ label: `Behavior: ${x.mood_behavior}`, tone: "info" });
  return chips;
}

export const CONCERN_LABEL: Record<NoteExtract["concern_level"], string> = {
  none: "No concern",
  low: "Low concern",
  med: "Medium concern",
  high: "High concern",
};
