"use client";

import { Activity, Footprints, Siren, WatchIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { BigButton, Panel } from "@/components/admin/admin-panel";
import { useNow } from "@/hooks/use-now";
import { postJSON } from "@/lib/api";
import { formatMinutes } from "@/lib/format";
import type { SimStatus } from "@/lib/types";
import { useLiveStore } from "@/store/live";

const KINDS = [
  { kind: "fall", label: "Inject fall", icon: Siren, tone: "red" as const, hint: "impact, then still on the floor (CRITICAL after ~35 s)" },
  { kind: "bed_exit", label: "Bed exit", icon: Footprints, tone: "default" as const, hint: "gets up and walks (fall-prevention at night)" },
  { kind: "sepsis_drift", label: "Sepsis drift", icon: Activity, tone: "default" as const, hint: "HR, RR, skin temp rise over 90 min" },
  { kind: "remove_band", label: "Remove band", icon: WatchIcon, tone: "default" as const, hint: "device task, never an alarm" },
];

type Props = { injections: SimStatus["injections"]; onDone: (ok: boolean, text: string) => void };

/** Scenario injectors (SPEC §8): start a scenario for one resident now. */
export function InjectorPanel({ injections, onDone }: Props) {
  const roster = useLiveStore((s) => s.roster);
  const residents = useMemo(() => Object.values(roster).sort((a, b) => a.room.localeCompare(b.room)), [roster]);
  const [rid, setRid] = useState(110);
  const [busy, setBusy] = useState<string | null>(null);
  const now = useNow();

  const inject = async (kind: string, label: string) => {
    setBusy(kind);
    try {
      await postJSON("/api/sim/inject", { kind, resident_id: rid });
      onDone(true, `${label}: ${roster[rid]?.name ?? `R-${rid}`} (room ${roster[rid]?.room ?? "?"})`);
    } catch (e) {
      onDone(false, `${label} failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Panel title="Scenario injectors">
      <label className="mb-3 flex flex-col gap-1 text-sm text-muted-foreground sm:max-w-md">
        Resident
        <select
          value={rid}
          onChange={(e) => setRid(Number(e.target.value))}
          className="h-12 rounded-lg border border-input bg-background/60 px-3 text-base text-foreground"
        >
          {residents.length === 0 && <option value={rid}>R-{rid}</option>}
          {residents.map((r) => (
            <option key={r.resident_id} value={r.resident_id}>
              {r.room} · {r.name} ({r.unit})
            </option>
          ))}
        </select>
      </label>
      <ul className="grid gap-2 sm:grid-cols-2">
        {KINDS.map((k) => (
          <li key={k.kind} className="flex flex-col gap-1">
            <BigButton icon={k.icon} tone={k.tone} disabled={busy !== null} onClick={() => inject(k.kind, k.label)}>
              {busy === k.kind ? "Injecting…" : k.label}
            </BigButton>
            <span className="text-sm text-muted-foreground">{k.hint}</span>
          </li>
        ))}
      </ul>
      <h3 className="mt-4 text-base font-bold">Active scenarios</h3>
      {injections.length === 0 ? (
        <p className="text-base text-muted-foreground">None.</p>
      ) : (
        <ul className="mt-1 space-y-1 text-base">
          {injections.map((i) => (
            <li key={`${i.kind}-${i.resident_id}-${i.start}`}>
              <span className="">{i.kind.replace("_", " ")}</span> · {roster[i.resident_id]?.name ?? `R-${i.resident_id}`}
              {now !== null && <span className="text-muted-foreground"> · ends in {formatMinutes((Date.parse(i.end) - now) / 60000)}</span>}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
