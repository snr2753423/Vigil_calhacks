// Alert chime via Web Audio (no asset files). Browsers only allow audio after a user gesture, so the
// context is created/resumed on the first tap anywhere (see LiveSocket).

let ctx: AudioContext | null = null;

export function unlockAudio(): void {
  try {
    ctx ??= new AudioContext();
    if (ctx.state === "suspended") void ctx.resume();
  } catch {
    ctx = null;
  }
}

export function audioReady(): boolean {
  return ctx?.state === "running";
}

/** Three descending tones (~0.9 s): distinct from a phone ring, not shrill. */
export function playCriticalChime(): void {
  if (!ctx || ctx.state !== "running") return;
  const start = ctx.currentTime + 0.02;
  [880, 740, 588].forEach((freq, i) => {
    const osc = ctx!.createOscillator();
    const gain = ctx!.createGain();
    const t = start + i * 0.28;
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.25, t + 0.03);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.26);
    osc.connect(gain).connect(ctx!.destination);
    osc.start(t);
    osc.stop(t + 0.3);
  });
}
