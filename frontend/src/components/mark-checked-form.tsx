"use client";

import { Check, Loader2 } from "lucide-react";
import { useState } from "react";
import { cn } from "cn";

import { postJSON } from "@/lib/api";
import { facilityClock12, firstName } from "@/lib/format";
import { useLiveStore } from "@/store/live";

const KINDS = [
  { value: "bedside", label: "Bedside check" },
  { value: "toileting", label: "Toileting" },
  { value: "repositioned", label: "Repositioned" },
  { value: "vitals", label: "Vitals taken" },
] as const;

type Props = { residentId: number; name: string; onLogged?: () => void; compact?: boolean };

/** "I checked on Rosa" → POST /api/checks (quiets the card for 30 min unless things get worse). `compact`: the
 *  big button first, the short form after a tap; otherwise the form is open. */
export function MarkCheckedForm({ residentId, name, onLogged, compact = false }: Props) {
  const [open, setOpen] = useState(!compact);
  const [kind, setKind] = useState<string>("bedside");
  const [note, setNote] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "done" | "error">("idle");
  const [doneAt, setDoneAt] = useState<number | null>(null);
  const [error, setError] = useState("");
  const replay = useLiveStore((s) => s.sim?.mode === "replay");
  const first = firstName(name);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setState("saving");
    try {
      const r = await postJSON<{ ts: string }>("/api/checks", {
        resident_id: residentId,
        kind,
        note: note.trim() || null,
      });
      setDoneAt(Date.parse(r.ts));
      setState("done");
      setNote("");
      if (compact) setOpen(false);
      onLogged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
      setState("error");
    }
  };

  const logged = state === "done" && doneAt !== null && (
    <span className="flex items-center gap-1.5 text-base text-stable" role="status">
      <Check className="size-5" aria-hidden /> Logged at {facilityClock12(doneAt)}
    </span>
  );

  if (replay) {
    return (
      <button type="button" disabled className="vg-btn-primary" title="Replay is read-only: checks are logged in live mode">
        I checked on {first}
        <span className="sr-only"> (replay is read-only)</span>
      </button>
    );
  }

  if (!open) {
    return (
      <>
        <button
          type="button"
          onClick={() => {
            setOpen(true);
            setState("idle");
          }}
          className="vg-btn-primary"
        >
          I checked on {first}
        </button>
        {logged}
      </>
    );
  }

  return (
    <form onSubmit={submit} className="w-full space-y-3 rounded-[14px] bg-surface-2 p-4">
      <fieldset>
        <legend className="mb-2 text-base text-muted-foreground">What did you do for {first}?</legend>
        <div className="flex flex-wrap gap-2">
          {KINDS.map((k) => (
            <label
              key={k.value}
              className={cn(
                "flex min-h-11 cursor-pointer items-center rounded-[10px] border px-3 text-base",
                kind === k.value ? "border-accent bg-surface font-bold" : "border-line bg-surface hover:border-soft-line",
              )}
            >
              <input
                type="radio"
                name={`kind-${residentId}`}
                value={k.value}
                checked={kind === k.value}
                onChange={() => setKind(k.value)}
                className="sr-only"
              />
              {k.label}
            </label>
          ))}
        </div>
      </fieldset>
      <label className="block">
        <span className="sr-only">Optional note</span>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={2000}
          placeholder="Optional: what you saw"
          className="vg-input"
        />
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" disabled={state === "saving"} className="vg-btn-primary">
          {state === "saving" ? <Loader2 className="size-5 animate-spin" aria-hidden /> : <Check className="size-5" aria-hidden />}
          Log check
        </button>
        {compact && (
          <button type="button" onClick={() => setOpen(false)} className="vg-btn-secondary">
            Cancel
          </button>
        )}
        {logged}
        {state === "error" && (
          <span className="text-base text-crit" role="alert">
            {error}
          </span>
        )}
      </div>
    </form>
  );
}
