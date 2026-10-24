"use client";

import { Check, Loader2, FileText, MessageSquarePlus, Mic, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cn } from "cn";

import { postForm, postJSON } from "@/lib/api";
import { audioExtension, CONCERN_LABEL, extractChips, pickRecorderType } from "@/lib/notes";
import type { NoteResponse } from "@/lib/types";
import { useLiveStore } from "@/store/live";
import { ROLES, usePrefs } from "@/store/prefs";

const MAX_RECORD_S = 60;

type State = "idle" | "recording" | "transcribing" | "saving" | "done" | "error";

/** Nurse note, typed or spoken → POST /api/notes. Voice: the browser records (webm on Chrome, mp4 on iPhone Safari),
 * Snowflake AI_TRANSCRIBE writes the text; then AI_EXTRACT reads either into fields (confusion, intake, concern)
 * that feed NEWS2 consciousness and P_concern. Shows the transcript and the extracted fields. */
export function AddNoteForm({ residentId, onSaved }: { residentId: number; onSaved: () => void }) {
  const role = usePrefs((s) => s.role);
  const [text, setText] = useState("");
  const [state, setState] = useState<State>("idle");
  const [error, setError] = useState("");
  const [result, setResult] = useState<NoteResponse | null>(null);
  const [seconds, setSeconds] = useState(0);
  const replay = useLiveStore((s) => s.sim?.mode === "replay");
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const noteRole = ROLES[role].noteRole;

  useEffect(
    () => () => {
      if (timer.current) clearInterval(timer.current);
      recorder.current?.stream.getTracks().forEach((t) => t.stop());
    },
    [],
  );

  const finish = (r: NoteResponse) => {
    setResult(r);
    setState("done");
    onSaved();
  };
  const fail = (e: unknown) => {
    setError(e instanceof Error ? e.message : "Could not save");
    setState("error");
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    setState("saving");
    setResult(null);
    try {
      finish(await postJSON<NoteResponse>("/api/notes", { resident_id: residentId, text, author_role: noteRole }));
      setText("");
    } catch (err) {
      fail(err);
    }
  };

  const upload = async (blob: Blob, mime: string) => {
    setState("transcribing");
    const form = new FormData();
    form.set("resident_id", String(residentId));
    form.set("author_role", noteRole);
    form.set("audio", blob, `note.${audioExtension(mime)}`);
    try {
      finish(await postForm<NoteResponse>("/api/notes", form));
    } catch (err) {
      fail(err);
    }
  };

  const startRecording = async () => {
    setError("");
    setResult(null);
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      fail(new Error("This browser can't record audio here (needs HTTPS). Type the note instead."));
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      fail(new Error("Microphone permission was denied. Allow it in the browser, or type the note."));
      return;
    }
    const mime = pickRecorderType((t) => MediaRecorder.isTypeSupported(t)) ?? "";
    let rec: MediaRecorder;
    try {
      rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    } catch {
      stream.getTracks().forEach((t) => t.stop());
      fail(new Error("This browser can't record audio here. Type the note instead."));
      return;
    }
    chunks.current = [];
    rec.ondataavailable = (ev) => {
      if (ev.data.size > 0) chunks.current.push(ev.data);
    };
    rec.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      if (timer.current) clearInterval(timer.current);
      const type = rec.mimeType || mime || "audio/webm";
      const blob = new Blob(chunks.current, { type });
      if (blob.size === 0) {
        fail(new Error("Nothing was recorded. Try again."));
        return;
      }
      void upload(blob, type);
    };
    recorder.current = rec;
    try {
      rec.start(250);
    } catch {
      stream.getTracks().forEach((t) => t.stop());
      fail(new Error("Could not start recording. Type the note instead."));
      return;
    }
    setSeconds(0);
    setState("recording");
    const started = Date.now();
    timer.current = setInterval(() => {
      const s = Math.floor((Date.now() - started) / 1000);
      setSeconds(s);
      if (s >= MAX_RECORD_S && rec.state === "recording") rec.stop();
    }, 250);
  };

  const stopRecording = () => {
    if (recorder.current?.state === "recording") recorder.current.stop();
  };

  if (replay) {
    return (
      <section className="space-y-1 vg-card p-5">
        <h2 className="flex items-center gap-2 text-xl font-bold">
          <MessageSquarePlus className="size-5" aria-hidden /> Add note
        </h2>
        <p className="text-base text-muted-foreground">Replay is read-only: notes are added in live mode.</p>
      </section>
    );
  }

  const busy = state === "saving" || state === "transcribing" || state === "recording";
  return (
    <form onSubmit={submit} className="space-y-3 vg-card p-5">
      <label htmlFor={`note-${residentId}`} className="flex items-center gap-2 text-xl font-bold">
        <MessageSquarePlus className="size-5" aria-hidden /> Add note
      </label>
      <textarea
        id={`note-${residentId}`}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          if (!busy) setState("idle");
        }}
        rows={3}
        maxLength={4000}
        disabled={state === "recording" || state === "transcribing"}
        placeholder="e.g. More confused than usual, only drank half her fluids."
        className="w-full rounded-lg border border-input bg-background/60 p-3 text-base"
      />
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="flex h-12 items-center gap-2 rounded-xl bg-surface-2 px-5 text-base font-bold hover:bg-surface-2 disabled:opacity-50"
        >
          {state === "saving" && <Loader2 className="size-5 animate-spin" aria-hidden />}
          Save note
        </button>
        {state === "recording" ? (
          <button
            type="button"
            onClick={stopRecording}
            className="flex h-12 items-center gap-2 rounded-xl bg-crit-bg px-5 text-base font-bold text-crit ring-1 ring-crit-solid hover:bg-crit-bg"
          >
            <span className="size-2.5 animate-pulse rounded-full bg-crit-solid" aria-hidden />
            <Square className="size-4" aria-hidden /> Stop · {seconds}s
          </button>
        ) : (
          <button
            type="button"
            onClick={startRecording}
            disabled={busy}
            className="flex h-12 items-center gap-2 rounded-xl bg-surface-2 px-5 text-base font-bold hover:bg-surface-2 disabled:opacity-50"
          >
            {state === "transcribing" ? <Loader2 className="size-5 animate-spin" aria-hidden /> : <Mic className="size-5" aria-hidden />}
            {state === "transcribing" ? "Transcribing…" : "Voice note"}
          </button>
        )}
        <span className="text-sm text-muted-foreground">as {noteRole}</span>
        {state === "recording" && (
          <span className="text-sm text-crit" role="status">
            Recording, speak now (stops at {MAX_RECORD_S} s)
          </span>
        )}
        {state === "transcribing" && (
          <span className="text-sm text-muted-foreground" role="status">
            Snowflake AI_TRANSCRIBE, then AI_EXTRACT…
          </span>
        )}
        {state === "error" && (
          <span className="text-sm text-crit" role="alert">
            {error}
          </span>
        )}
      </div>
      {state === "done" && result && <NoteResult r={result} />}
    </form>
  );
}

const CHIP_TONE = {
  alert: "bg-crit-bg text-crit ring-crit-solid",
  warn: "bg-high-bg text-high ring-high-solid",
  info: "bg-surface-2 text-muted-foreground ring-line",
} as const;

function NoteResult({ r }: { r: NoteResponse }) {
  const x = r.extract;
  const chips = extractChips(x);
  const snowflake = x.source === "snowflake";
  return (
    <div className="space-y-2 rounded-xl bg-surface-2 p-3 border border-line" role="status">
      <p className="flex items-center gap-1.5 text-sm text-stable">
        <Check className="size-4" aria-hidden /> Saved {r.transcript ? "voice note" : "note"}
      </p>
      {r.transcript && (
        <blockquote className="border-l-2 border-line pl-3 text-base">
          “{r.transcript.text}”
          <span className="block text-sm text-muted-foreground">
            Snowflake AI_TRANSCRIBE · {r.transcript.audio_duration ? `${r.transcript.audio_duration.toFixed(1)} s audio · ` : ""}
            {(r.transcript.ms / 1000).toFixed(1)} s
          </span>
        </blockquote>
      )}
      <div className="flex flex-wrap gap-2">
        <span
          className={cn(
            "inline-flex min-h-8 items-center rounded-lg px-2.5 text-sm font-bold ring-1",
            x.concern_level === "high" || x.concern_level === "med" ? CHIP_TONE.alert : x.concern_level === "low" ? CHIP_TONE.warn : CHIP_TONE.info,
          )}
        >
          {CONCERN_LABEL[x.concern_level]}
        </span>
        {chips.map((c) => (
          <span key={c.label} className={cn("inline-flex min-h-8 items-center rounded-lg px-2.5 text-sm ring-1", CHIP_TONE[c.tone])}>
            {c.label}
          </span>
        ))}
      </div>
      <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
        <FileText className="size-4" aria-hidden />
        {snowflake
          ? `Snowflake AI_EXTRACT${x.ms ? ` · ${(x.ms / 1000).toFixed(1)} s` : ""} · feeds NEWS2 consciousness and staff concern`
          : r.extract_status === "pending"
            ? "Keyword reading for now: Snowflake AI_EXTRACT is still running and will update the score"
            : `Keyword fallback: ${r.extract_status}`}
      </p>
    </div>
  );
}
