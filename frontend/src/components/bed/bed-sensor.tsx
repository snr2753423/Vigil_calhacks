"use client";

import { BedDouble, Footprints, Loader2, Play, Ruler, Square, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "cn";

import { calibrationAt, CALIBRATION_STEPS, LEVELS, meterFill, summarize, vec, type MotionSample } from "@/lib/bed";
import { IngestSocket, type IngestReply, type IngestStatus } from "@/lib/ingest-socket";
import type { BedCalibration } from "@/lib/types";

type BedReply = {
  accepted: boolean;
  reason?: string;
  in_bed: boolean | null;
  state: string;
  level: number;
  calibrating: string | null;
  mode: BedCalibration["mode"];
  calibrated: boolean;
  transition: "bed_exit" | "bed_return" | null;
};

type DM = { requestPermission?: () => Promise<"granted" | "denied"> };

const MODE_TEXT: Record<BedCalibration["mode"], string> = {
  tilt: "tilt · exit in ~2 s",
  energy: "breathing motion · exit in ~4 s",
  burst: "burst + 10 s still",
};

function say(text: string) {
  try {
    const synth = window.speechSynthesis;
    if (!synth) return;
    synth.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.05;
    synth.speak(u);
  } catch {
    // speech is a nicety
  }
}

/** iPhone as an under-pillow bed sensor (SPEC §10, §11.6): DeviceMotion → 1-s summaries → /ws/ingest; the
 * backend's state machine answers with in bed / out of bed. Needs HTTPS and a tap to start (iOS). */
export function BedSensor({ residentId }: { residentId: number }) {
  const [running, setRunning] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ingest, setIngest] = useState<IngestStatus | "off">("off");
  const [bed, setBed] = useState<BedReply | null>(null);
  const [cal, setCal] = useState<BedCalibration | null>(null);
  const [calibMsg, setCalibMsg] = useState<string | null>(null);
  const [energy, setEnergy] = useState(0);
  const [rate, setRate] = useState(0);
  const [wake, setWake] = useState<"on" | "off" | "unsupported">("off");
  const [step, setStep] = useState<{ index: number; remainingS: number } | null>(null);
  const [lastChange, setLastChange] = useState<{ kind: string; at: number } | null>(null);

  const socket = useRef<IngestSocket | null>(null);
  const buffer = useRef<MotionSample[]>([]);
  const tick = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const lock = useRef<WakeLockSentinel | null>(null);
  const calib = useRef<{ start: number; sent: number } | null>(null);

  const onMotion = useCallback((e: DeviceMotionEvent) => {
    buffer.current.push({ a: vec(e.acceleration), g: vec(e.accelerationIncludingGravity) });
  }, []);

  const requestWake = useCallback(async () => {
    if (!("wakeLock" in navigator)) {
      setWake("unsupported");
      return;
    }
    try {
      lock.current = await navigator.wakeLock.request("screen");
      setWake("on");
      lock.current.addEventListener("release", () => setWake("off"));
    } catch {
      setWake("off");
    }
  }, []);

  const onReply = useCallback((msg: IngestReply) => {
    if (msg.type === "hello") {
      setCal((msg.calibration as BedCalibration) ?? null);
      if (msg.accepted === false) setError(String(msg.reason));
    } else if (msg.type === "bed") {
      const r = msg as unknown as BedReply;
      setBed(r);
      setError(r.accepted ? null : (r.reason ?? "Rejected"));
      if (r.transition) {
        setLastChange({ kind: r.transition, at: Date.now() });
        if (!calib.current) say(r.transition === "bed_exit" ? "Out of bed" : "Back in bed");
      }
    } else if (msg.type === "calibration") {
      if (msg.status === "done") {
        setCal(msg.calibration as BedCalibration);
        setCalibMsg(null);
      } else if (msg.status === "failed") {
        setCalibMsg(`Calibration failed: ${String(msg.error)}`);
      }
    } else if (msg.type === "error") {
      setError(String(msg.message));
    }
  }, []);

  const everySecond = useCallback(() => {
    const samples = buffer.current.splice(0);
    setRate(samples.length);
    const s = summarize(samples);
    if (s) {
      socket.current?.send({ ts: Date.now(), ...s });
      setEnergy(s.motion_energy);
    }
    const c = calib.current;
    if (!c) return;
    const at = calibrationAt((Date.now() - c.start) / 1000);
    if (at === null) {
      calib.current = null;
      setStep(null);
      socket.current?.send({ type: "calibrate", action: "finish" });
      say("Calibration done. You're in bed.");
      return;
    }
    setStep(at);
    if (at.index !== c.sent) {
      c.sent = at.index;
      const st = CALIBRATION_STEPS[at.index];
      socket.current?.send({ type: "calibrate", action: "begin", phase: st.phase });
      if (at.index > 0) say(st.say); // step 0 was spoken by the tap itself (iOS needs a gesture for speech)
    }
  }, []);

  const start = async () => {
    setError(null);
    if (!window.isSecureContext) {
      setError("Motion sensors need HTTPS: open this page through the tunnel (the QR code on /devices).");
      return;
    }
    const motion = (window as unknown as { DeviceMotionEvent?: DM }).DeviceMotionEvent;
    if (!motion) {
      setError("This browser has no motion sensor access.");
      return;
    }
    setStarting(true);
    try {
      if (typeof motion.requestPermission === "function") {
        // iOS 13+: must be called from the tap
        const res = await motion.requestPermission();
        if (res !== "granted") {
          setError("Motion access was denied. Close the tab, reopen the link and tap Allow (or enable Motion & Orientation Access in Settings → Safari).");
          return;
        }
      }
      window.addEventListener("devicemotion", onMotion);
      await requestWake();
      const onStatus = (st: IngestStatus) => {
        setIngest(st);
        if (st !== "open" && calib.current) {
          // the backend cancels a calibration whose socket dropped: stop the steps here too
          calib.current = null;
          setStep(null);
          setCalibMsg("The connection dropped during calibration. Tap Calibrate again.");
          say("Calibration interrupted.");
        }
      };
      socket.current = new IngestSocket({ device: "phone", residentId, onStatus, onMessage: onReply });
      tick.current = setInterval(everySecond, 1000);
      setRunning(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const stop = useCallback(() => {
    window.removeEventListener("devicemotion", onMotion);
    clearInterval(tick.current);
    socket.current?.close();
    socket.current = null;
    void lock.current?.release().catch(() => undefined);
    lock.current = null;
    calib.current = null;
    setStep(null);
    setRunning(false);
    setIngest("off");
  }, [onMotion]);

  const calibrate = () => {
    setCalibMsg(null);
    calib.current = { start: Date.now(), sent: -1 };
    say(CALIBRATION_STEPS[0].say);
    everySecond();
  };

  const cancelCalibration = () => {
    calib.current = null;
    setStep(null);
    socket.current?.send({ type: "calibrate", action: "cancel" });
    say("Calibration cancelled.");
  };

  useEffect(() => {
    // iOS drops the wake lock when the page is hidden: take it again when it comes back
    const onVisible = () => {
      if (document.visibilityState === "visible" && socket.current) void requestWake();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      stop();
    };
  }, [requestWake, stop]);

  const calibrating = step !== null;
  const cur = step ? CALIBRATION_STEPS[step.index] : null;
  const fill = meterFill(energy, cal?.quiet_energy ?? 0.05);
  const out = bed?.in_bed === false;

  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col gap-4 px-4 py-5">
      <header className="flex items-baseline justify-between">
        <h1 className="text-xl font-bold">
          <span className="text-accent">Vigil</span> bed sensor · R-{residentId}
        </h1>
        <span className={cn("text-sm", ingest === "open" ? "text-accent" : "text-muted-foreground")}>
          {ingest === "off" ? "stopped" : ingest === "open" ? "● streaming" : `○ ${ingest}`}
        </span>
      </header>

      {!running ? (
        <section className="space-y-4 vg-card p-5">
          <p className="text-lg">This phone becomes R-{residentId}&apos;s under-mattress bed sensor.</p>
          <ol className="list-decimal space-y-1 pl-5 text-base text-muted-foreground">
            <li>Tap Start sensor and allow motion access.</li>
            <li>Tap Calibrate and follow the spoken steps (about 35 s).</li>
            <li>Keep the screen on; turn the volume up for the voice prompts.</li>
          </ol>
          <button
            type="button"
            onClick={() => void start()}
            disabled={starting}
            className="flex h-16 w-full items-center justify-center gap-2 rounded-2xl bg-accent text-xl font-bold text-accent-ink disabled:opacity-60"
          >
            {starting ? <Loader2 className="size-6 animate-spin" aria-hidden /> : <Play className="size-6" aria-hidden />} Start sensor
          </button>
        </section>
      ) : (
        <>
          <section
            aria-live="polite"
            className={cn(
              "rounded-2xl p-5 text-center ring-1",
              calibrating
                ? "bg-surface-2 ring-line"
                : out
                  ? "bg-high-bg ring-high-solid"
                  : "bg-surface-2 ring-accent",
            )}
          >
            {calibrating && cur ? (
              <>
                <p className="text-sm uppercase tracking-wide text-foreground">
                  Calibrating · step {step.index + 1} of {CALIBRATION_STEPS.length}
                </p>
                <p className="mt-2 text-2xl font-bold">{cur.title}</p>
                <p className="mt-2 text-6xl tabular-nums">{step.remainingS}</p>
              </>
            ) : (
              <>
                {out ? <Footprints className="mx-auto size-12 text-high" aria-hidden /> : <BedDouble className="mx-auto size-12 text-accent" aria-hidden />}
                <p className="mt-2 text-3xl font-bold">
                  {bed === null ? "Waiting for data…" : out ? "Out of bed" : `In bed · ${LEVELS[bed.level] ?? ""}`}
                </p>
                {lastChange && (
                  <p className="mt-1 text-base text-muted-foreground">
                    {lastChange.kind === "bed_exit" ? "Got up" : "Back in bed"} at{" "}
                    {new Date(lastChange.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                  </p>
                )}
              </>
            )}
          </section>

          <section className="space-y-2 vg-card p-5">
            <div className="flex items-baseline justify-between text-sm text-muted-foreground">
              <span>Motion</span>
              <span className="tabular-nums">
                {energy.toFixed(3)} m/s² · {rate} Hz
              </span>
            </div>
            <div className="h-6 overflow-hidden rounded-full bg-surface-2" role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={fill} aria-label="Motion">
              <div
                className={cn("h-full rounded-full transition-[width] duration-300", fill > 0.75 ? "bg-high-solid" : "bg-accent")}
                style={{ width: `${Math.round(fill * 100)}%` }}
              />
            </div>
            <dl className="grid grid-cols-2 gap-2 pt-1 text-sm">
              <div>
                <dt className="text-muted-foreground">Detection</dt>
                <dd>{cal?.calibrated ? MODE_TEXT[cal.mode] : "not calibrated"}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Screen awake</dt>
                <dd>{wake === "on" ? "yes" : wake === "unsupported" ? "set Auto-Lock to Never" : "no — tap the screen"}</dd>
              </div>
            </dl>
          </section>

          {cal?.warnings?.map((w) => <Warn key={w}>{w}</Warn>)}
          {calibMsg && <Warn>{calibMsg}</Warn>}

          <div className="grid grid-cols-2 gap-3">
            {calibrating ? (
              <button type="button" onClick={cancelCalibration} className="col-span-2 flex h-14 items-center justify-center gap-2 rounded-xl bg-surface-2 text-lg font-bold">
                Cancel calibration
              </button>
            ) : (
              <>
                <button type="button" onClick={calibrate} disabled={ingest !== "open"} className="flex h-14 items-center justify-center gap-2 rounded-xl bg-surface-2 text-lg font-bold border border-line disabled:opacity-50">
                  <Ruler className="size-5" aria-hidden /> Calibrate
                </button>
                <button type="button" onClick={stop} className="flex h-14 items-center justify-center gap-2 rounded-xl bg-surface-2 text-lg font-bold">
                  <Square className="size-5" aria-hidden /> Stop
                </button>
              </>
            )}
          </div>
        </>
      )}

      {error && <Warn>{error}</Warn>}
    </main>
  );
}

function Warn({ children }: { children: React.ReactNode }) {
  return (
    <p role="alert" className="flex items-start gap-2 rounded-xl bg-high-bg px-3 py-2 text-base text-high ring-1 ring-high-solid">
      <TriangleAlert className="mt-0.5 size-5 shrink-0" aria-hidden /> <span>{children}</span>
    </p>
  );
}
