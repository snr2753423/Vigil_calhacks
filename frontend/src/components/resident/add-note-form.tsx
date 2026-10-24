"use client";

import { Check, Loader2, MessageSquarePlus } from "lucide-react";
import { useState } from "react";

import { postJSON } from "@/lib/api";
import { useLiveStore } from "@/store/live";
import { ROLES, usePrefs } from "@/store/prefs";

/** Typed nurse note → POST /api/notes (a `note_added` event that feeds P_concern / NEWS2 consciousness). */
export function AddNoteForm({ residentId, onSaved }: { residentId: number; onSaved: () => void }) {
  const role = usePrefs((s) => s.role);
  const [text, setText] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "done" | "error">("idle");
  const [error, setError] = useState("");
  const replay = useLiveStore((s) => s.sim?.mode === "replay");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    setState("saving");
    try {
      await postJSON("/api/notes", { resident_id: residentId, text, author_role: ROLES[role].noteRole });
      setText("");
      setState("done");
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
      setState("error");
    }
  };

  if (replay) {
    return (
      <section className="space-y-1 rounded-2xl bg-card p-4 ring-1 ring-white/10">
        <h2 className="flex items-center gap-2 text-xl font-semibold">
          <MessageSquarePlus className="size-5" aria-hidden /> Add note
        </h2>
        <p className="text-base text-muted-foreground">Replay is read-only: notes are added in live mode.</p>
      </section>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-2 rounded-2xl bg-card p-4 ring-1 ring-white/10">
      <label htmlFor={`note-${residentId}`} className="flex items-center gap-2 text-xl font-semibold">
        <MessageSquarePlus className="size-5" aria-hidden /> Add note
      </label>
      <textarea
        id={`note-${residentId}`}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          if (state !== "saving") setState("idle");
        }}
        rows={3}
        maxLength={4000}
        placeholder="e.g. More confused than usual, only drank half her fluids."
        className="w-full rounded-lg border border-input bg-background/60 p-3 text-base"
      />
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={state === "saving" || !text.trim()}
          className="flex h-12 items-center gap-2 rounded-xl bg-white/10 px-5 text-base font-semibold hover:bg-white/15 disabled:opacity-50"
        >
          {state === "saving" && <Loader2 className="size-5 animate-spin" aria-hidden />}
          Save note
        </button>
        <span className="text-sm text-muted-foreground">as {ROLES[role].noteRole} · voice notes come later</span>
        {state === "done" && (
          <span className="flex items-center gap-1 text-sm text-emerald-200" role="status">
            <Check className="size-4" aria-hidden /> Saved
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
