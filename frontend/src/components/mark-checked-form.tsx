"use client";

import { Check, ClipboardCheck, Loader2 } from "lucide-react";
import { useState } from "react";
import { cn } from "cn";

import { postJSON } from "@/lib/api";
import { facilityHHMM } from "@/lib/format";

const KINDS = [
  { value: "bedside", label: "Bedside check" },
  { value: "toileting", label: "Toileting" },
  { value: "repositioned", label: "Repositioned" },
  { value: "vitals", label: "Vitals taken" },
] as const;

type Props = { residentId: number; name: string; onLogged?: () => void; compact?: boolean };

/** "Mark checked" quick form → POST /api/checks (quiets the card for 30 min unless things get worse). */
export function MarkCheckedForm({ residentId, name, onLogged, compact = false }: Props) {
  const [open, setOpen] = useState(!compact);
  const [kind, setKind] = useState<string>("bedside");
  const [note, setNote] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "done" | "error">("idle");
  const [doneAt, setDoneAt] = useState<number | null>(null);
  const [error, setError] = useState("");

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

  if (!open) {
    return (
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => {
            setOpen(true);
            setState("idle");
          }}
          className="flex h-12 items-center gap-2 rounded-xl bg-teal-500/15 px-4 text-base font-semibold text-teal-100 ring-1 ring-teal-400/40 hover:bg-teal-500/25"
        >
          <ClipboardCheck className="size-5" aria-hidden /> Mark checked
        </button>
        {state === "done" && doneAt !== null && (
          <span className="flex items-center gap-1 text-sm text-emerald-200" role="status">
            <Check className="size-4" aria-hidden /> Logged {facilityHHMM(doneAt)}
          </span>
        )}
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="w-full space-y-3 rounded-xl bg-white/[0.04] p-3 ring-1 ring-white/10">
      <fieldset>
        <legend className="mb-2 text-sm text-muted-foreground">Check for {name}</legend>
        <div className="flex flex-wrap gap-2">
          {KINDS.map((k) => (
            <label
              key={k.value}
              className={cn(
                "flex h-11 cursor-pointer items-center rounded-lg px-3 text-base ring-1",
                kind === k.value ? "bg-teal-500/25 text-teal-50 ring-teal-300/60" : "ring-white/15 hover:bg-white/5",
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
          className="h-11 w-full rounded-lg border border-input bg-background/60 px-3 text-base"
        />
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="submit"
          disabled={state === "saving"}
          className="flex h-12 items-center gap-2 rounded-xl bg-teal-400 px-5 text-base font-semibold text-teal-950 hover:bg-teal-300 disabled:opacity-60"
        >
          {state === "saving" ? <Loader2 className="size-5 animate-spin" aria-hidden /> : <Check className="size-5" aria-hidden />}
          Log check
        </button>
        {compact && (
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="h-12 rounded-xl px-4 text-base text-muted-foreground hover:bg-white/5"
          >
            Cancel
          </button>
        )}
        {state === "done" && doneAt !== null && (
          <span className="flex items-center gap-1 text-sm text-emerald-200" role="status">
            <Check className="size-4" aria-hidden /> Logged {facilityHHMM(doneAt)}
          </span>
        )}
        {state === "error" && (
          <span className="text-sm text-red-200" role="alert">
            {error}
          </span>
        )}
      </div>
    </form>
  );
}
