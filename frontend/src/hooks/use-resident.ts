"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getJSON } from "@/lib/api";
import type { ResidentDetail } from "@/lib/types";
import { useLiveStore } from "@/store/live";

const REFRESH_MS = 15_000;

/** GET /api/residents/{id} (profile, baselines, full score breakdown, 24 h events), refreshed every 15 s
 *  and right after a check or note for this resident arrives on /ws/live. */
export function useResident(id: number) {
  const [data, setData] = useState<ResidentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false); // a manual Refresh only, not the background poll
  const ctrl = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    try {
      const d = await getJSON<ResidentDetail>(`/api/residents/${id}`, c.signal);
      setData(d);
      setError(null);
    } catch (e) {
      if (!c.signal.aborted) setError(e instanceof Error ? e.message : "Could not load");
    } finally {
      if (!c.signal.aborted) setRefreshing(false);
    }
  }, [id]);

  const refresh = useCallback(() => {
    setRefreshing(true);
    void load();
  }, [load]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    const t = setInterval(load, REFRESH_MS);
    return () => {
      clearTimeout(first);
      clearInterval(t);
      ctrl.current?.abort();
    };
  }, [load]);

  const lastEvent = useLiveStore((s) => s.lastEvent);
  useEffect(() => {
    if (lastEvent?.event.resident_id === id) {
      const t = setTimeout(load, 800); // let the evaluator re-score first
      return () => clearTimeout(t);
    }
  }, [lastEvent, id, load]);

  return { data, error, refreshing, reload: load, refresh };
}
