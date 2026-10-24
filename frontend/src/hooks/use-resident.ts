"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useReplayCursor } from "@/hooks/use-clock";
import { getJSON, postJSON } from "@/lib/api";
import type { Assessment, ResidentDetail } from "@/lib/types";
import { useLiveStore } from "@/store/live";

const REFRESH_MS = 15_000;
const REPLAY_RELOAD_MS = 2_500; // during a replay: follow the cursor, at most this often
const ASSESS_WAIT_MS = 60_000; // give up showing "updating" if no Cortex result arrives by then

/** GET /api/residents/{id} (profile, baselines, full score breakdown, 24 h events, AI card), refreshed every
 *  15 s and right after a check, note or new Cortex assessment for this resident arrives on /ws/live.
 *  `refresh` asks Snowflake Cortex for a new assessment (POST /api/explain/{id}). */
export function useResident(id: number) {
  const [data, setData] = useState<ResidentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false); // a manual Refresh only, not the background poll
  const ctrl = useRef<AbortController | null>(null);
  const waitTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stopWaiting = useCallback(() => {
    if (waitTimer.current) clearTimeout(waitTimer.current);
    waitTimer.current = null;
    setRefreshing(false);
  }, []);

  const load = useCallback(async () => {
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    try {
      const d = await getJSON<ResidentDetail>(`/api/residents/${id}`, c.signal);
      setData(d);
      setError(null);
      // Waiting on a Refresh, but the server no longer has a Cortex call running (it failed): stop waiting.
      if (waitTimer.current && !d.assessment.pending) stopWaiting();
    } catch (e) {
      if (!c.signal.aborted) setError(e instanceof Error ? e.message : "Could not load");
    }
  }, [id, stopWaiting]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    let a: Assessment;
    try {
      a = await postJSON<Assessment>(`/api/explain/${id}`);
    } catch {
      stopWaiting(); // the backend itself is unreachable: keep the card as it is
      return;
    }
    setData((d) => {
      if (!d) return d;
      // Cortex is still working: keep showing the current card (marked pending) rather than flashing the template.
      if (a.pending) return { ...d, assessment: { ...d.assessment, pending: true } };
      // Cortex failed: a Cortex card we already have is still the better card than the template.
      if (a.source === "template" && d.assessment.source === "cortex") {
        return { ...d, assessment: { ...d.assessment, pending: false } };
      }
      return { ...d, assessment: a };
    });
    if (a.pending) {
      if (waitTimer.current) clearTimeout(waitTimer.current);
      waitTimer.current = setTimeout(stopWaiting, ASSESS_WAIT_MS);
    } else {
      stopWaiting();
    }
  }, [id, stopWaiting]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    const t = setInterval(load, REFRESH_MS);
    return () => {
      clearTimeout(first);
      clearInterval(t);
      ctrl.current?.abort();
      if (waitTimer.current) clearTimeout(waitTimer.current);
    };
  }, [load]);

  const lastEvent = useLiveStore((s) => s.lastEvent);
  useEffect(() => {
    if (lastEvent?.event.resident_id === id) {
      const t = setTimeout(load, 800); // let the evaluator re-score first
      return () => clearTimeout(t);
    }
  }, [lastEvent, id, load]);

  // Replay: the backend scores this resident at the cursor; reload as the cursor moves (throttled).
  const cursor = useReplayCursor();
  const lastReplayLoad = useRef(0);
  useEffect(() => {
    if (cursor === null) return;
    const wait = Math.max(0, lastReplayLoad.current + REPLAY_RELOAD_MS - Date.now());
    const t = setTimeout(() => {
      lastReplayLoad.current = Date.now();
      void load();
    }, wait);
    return () => clearTimeout(t);
  }, [cursor, load]);

  const lastInsight = useLiveStore((s) => s.lastInsight);
  useEffect(() => {
    if (lastInsight?.resident_id === id && lastInsight.live) {
      const t = setTimeout(() => {
        stopWaiting();
        void load();
      }, 0);
      return () => clearTimeout(t);
    }
  }, [lastInsight, id, load, stopWaiting]);

  return { data, error, refreshing, reload: load, refresh };
}
