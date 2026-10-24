"use client";

import { useEffect, useState } from "react";

import { useClock } from "@/hooks/use-clock";
import { getJSON } from "@/lib/api";
import { facilityHour, shiftName } from "@/lib/format";
import type { StaffMember } from "@/lib/types";

const REFRESH_MS = 60_000;

/** Header line: "Night shift · Linda (RN), Daniel (LPN), 3 aides" (who is on shift by the displayed clock). */
export function StaffOnDuty({ className = "" }: { className?: string }) {
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const { now, offsetS } = useClock();

  useEffect(() => {
    let stop = false;
    const load = () =>
      getJSON<{ staff: StaffMember[] }>("/api/staff")
        .then((r) => !stop && setStaff(r.staff.filter((s) => s.on_duty)))
        .catch(() => undefined);
    void load();
    const t = setInterval(load, REFRESH_MS);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, []);

  const shift = now === null ? null : shiftName(facilityHour(now + offsetS * 1000));
  const lead = staff.filter((s) => s.role !== "CNA");
  const aides = staff.filter((s) => s.role === "CNA").length;
  const people = [
    ...lead.map((s) => `${s.name.split(" ")[0]} (${s.role})`),
    ...(aides ? [`${aides} aide${aides === 1 ? "" : "s"}`] : []),
  ];
  if (!shift) return null;
  return (
    <p className={className} title={staff.map((s) => `${s.role} ${s.name}${s.unit ? ` (${s.unit})` : ""}`).join("\n")}>
      {shift} shift{people.length ? ` · ${people.join(", ")}` : ""}
    </p>
  );
}
