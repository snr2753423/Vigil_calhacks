import { describe, expect, it } from "vitest";

import { audioExtension, extractChips, pickRecorderType } from "@/lib/notes";
import type { NoteExtract } from "@/lib/types";

const base: NoteExtract = {
  confusion_change: "unknown", oral_intake: "unknown", pain_0_10: null, urinary_symptoms: null,
  fall_or_near_fall: false, breathing_concern: false, mood_behavior: null, fever_or_warm: false,
  concern_level: "none", one_line_summary: "", source: "snowflake",
};

describe("recorder format", () => {
  it("prefers webm/opus (Chrome) and falls back to mp4 (iPhone Safari)", () => {
    expect(pickRecorderType((t) => t.startsWith("audio/webm"))).toBe("audio/webm;codecs=opus");
    expect(pickRecorderType((t) => t === "audio/mp4")).toBe("audio/mp4");
    expect(pickRecorderType(() => false)).toBeNull();
  });
  it("names the upload by container", () => {
    expect(audioExtension("audio/webm;codecs=opus")).toBe("webm");
    expect(audioExtension("audio/mp4")).toBe("m4a");
    expect(audioExtension("audio/ogg;codecs=opus")).toBe("ogg");
  });
});

describe("extracted chips", () => {
  it("leads with new/worse confusion and its NEWS2 effect", () => {
    const chips = extractChips({ ...base, confusion_change: "worse", oral_intake: "reduced" });
    expect(chips[0]).toEqual({ label: "Confusion: worse → NEWS2 consciousness +3", tone: "alert" });
    expect(chips.map((c) => c.label)).toContain("Intake: reduced");
  });
  it("leaves out unknown fields", () => {
    expect(extractChips(base)).toEqual([]);
  });
  it("flags falls, breathing and warmth", () => {
    const labels = extractChips({ ...base, fall_or_near_fall: true, breathing_concern: true, fever_or_warm: true }).map((c) => c.label);
    expect(labels).toEqual(["Fall or near-fall", "Breathing concern", "Warm / fever"]);
  });
});
