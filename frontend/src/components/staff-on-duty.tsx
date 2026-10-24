"use client";

import { Users } from "lucide-react";
import { useEffect, useState } from "react";

import { getJSON } from "@/lib/api";
import type { StaffMember } from "@/lib/types";

const REFRESH_MS = 60_000;

/** Header line: who is on shift now (by the displayed facility clock). */
export function StaffOnDuty() {
  const [staff, setStaff] = useState<StaffMember[]>([]);

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

  if (!staff.length) return null;
  const lead = staff.filter((s) => s.role !== "CNA");
  const cnas = staff.filter((s) => s.role === "CNA");
  return (
    <p
      className="hidden items-center gap-1.5 text-sm text-muted-foreground xl:flex"
      title={staff.map((s) => `${s.role} ${s.name}${s.unit ? ` (${s.unit})` : ""}`).join("\n")}
    >
      <Users className="size-4" aria-hidden />
      <span className="sr-only">On duty:</span>
      {lead.map((s) => `${s.role} ${s.name.split(" ")[0]}`).join(" · ")}
      {cnas.length > 0 && ` · ${cnas.length} CNAs`}
    </p>
  );
}
