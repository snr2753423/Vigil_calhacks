// iPhone bed sensor (SPEC §10): DeviceMotion samples (20–60 Hz) → one summary per second for /ws/ingest.
// The backend state machine (app/devices/bed_state.py) turns the summaries into in-bed / exit / return.

export type Vec3 = { x: number; y: number; z: number };

export type MotionSample = {
  /** Linear acceleration without gravity (m/s²); null on devices without a gyroscope. */
  a: Vec3 | null;
  /** Acceleration including gravity (m/s²): its mean over a second is the phone's resting tilt. */
  g: Vec3 | null;
};

export type BedSummary = {
  motion_energy: number;
  peak_jolt: number;
  gravity: [number, number, number] | null;
};

const mag = (v: Vec3) => Math.hypot(v.x, v.y, v.z);
const round = (v: number, d: number) => Math.round(v * 10 ** d) / 10 ** d;

/** RMS and peak of the linear acceleration over the window, and the mean gravity vector. Without linear
 *  acceleration it is estimated by removing the window's mean from acceleration-including-gravity. */
export function summarize(samples: MotionSample[]): BedSummary | null {
  const gs = samples.map((s) => s.g).filter((g): g is Vec3 => g !== null);
  const mean =
    gs.length > 0
      ? {
          x: gs.reduce((t, g) => t + g.x, 0) / gs.length,
          y: gs.reduce((t, g) => t + g.y, 0) / gs.length,
          z: gs.reduce((t, g) => t + g.z, 0) / gs.length,
        }
      : null;
  const linear: number[] = [];
  for (const s of samples) {
    if (s.a) linear.push(mag(s.a));
    else if (s.g && mean) linear.push(mag({ x: s.g.x - mean.x, y: s.g.y - mean.y, z: s.g.z - mean.z }));
  }
  if (linear.length === 0) return null;
  const rms = Math.sqrt(linear.reduce((t, v) => t + v * v, 0) / linear.length);
  return {
    motion_energy: round(rms, 5),
    peak_jolt: round(Math.max(...linear), 4),
    gravity: mean ? [round(mean.x, 5), round(mean.y, 5), round(mean.z, 5)] : null,
  };
}

/** A DeviceMotionEvent's numbers, or null when the browser left them out. */
export function vec(v: { x: number | null; y: number | null; z: number | null } | null | undefined): Vec3 | null {
  if (!v || v.x === null || v.y === null || v.z === null) return null;
  return { x: v.x, y: v.y, z: v.z };
}

// --------------------------------------------------------------------------- calibration steps

/** Recorded phases; "prep" pauses (placing the phone, lying down) are not recorded, so that motion can't
 *  inflate the thresholds. */
export type CalibrationPhase = "prep" | "empty" | "still" | "shift";

/** The phone is under the pillow for most of this, so each step is also spoken (`say`). */
export const CALIBRATION_STEPS: { phase: CalibrationPhase; seconds: number; title: string; say: string }[] = [
  { phase: "prep", seconds: 8, title: "Put the phone under the pillow and step away", say: "Put the phone under the pillow, then step away from the bed." },
  { phase: "empty", seconds: 5, title: "Bed empty: don't touch the bed", say: "Recording the empty bed. Don't touch it." },
  { phase: "prep", seconds: 6, title: "Lie down", say: "Now lie down." },
  { phase: "still", seconds: 10, title: "Lie still and breathe normally", say: "Stay still." },
  { phase: "shift", seconds: 5, title: "Roll over or shift your weight", say: "Now roll over or shift your weight." },
];

export const CALIBRATION_TOTAL_S = CALIBRATION_STEPS.reduce((t, s) => t + s.seconds, 0);

/** Which step is running `elapsedS` seconds into calibration, and how long it has left; null when done. */
export function calibrationAt(elapsedS: number): { index: number; remainingS: number } | null {
  let start = 0;
  for (let i = 0; i < CALIBRATION_STEPS.length; i++) {
    const end = start + CALIBRATION_STEPS[i].seconds;
    if (elapsedS < end) return { index: i, remainingS: Math.ceil(end - elapsedS) };
    start = end;
  }
  return null;
}

export const LEVELS = ["Still", "Some movement", "Restless", "Very restless"] as const;

/** 0–1 meter fill for a motion energy relative to the calibrated quiet threshold (log scale, so lying
 *  still sits low and a jolt pins it). */
export function meterFill(energy: number, quiet: number): number {
  if (!(energy > 0) || !(quiet > 0)) return 0;
  const r = Math.log10(energy / quiet); // −1 at a tenth of quiet, +1.5 at ~30×
  return Math.min(1, Math.max(0, (r + 1) / 2.5));
}
