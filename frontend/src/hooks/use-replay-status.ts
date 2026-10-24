"use client";

import { useCallback, useEffect, useState } from "react";

import { getJSON } from "@/lib/api";
import type { ReplayStatus } from "@/lib/types";
import { useLiveStore } from "@/store/live";

const FAST_MS = 1500; // while Prepare replay runs
const SLOW_MS = 5000;

/** GET /api/replay (bookmarks, prepared summary, prepare progress), polled; faster while preparing. */
export function useReplayStatus(enabled = true) {
  const [status, setStatus] = useState<ReplayStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mode = useLiveStore((s) => s.sim?.mode);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getJSON<ReplayStatus>("/api/replay"));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the replay status");
    }
  }, []);

  const preparing = status?.prepare.running ?? false;
  useEffect(() => {
    if (!enabled) return;
    const first = setTimeout(refresh, 0);
    const t = setInterval(refresh, preparing ? FAST_MS : SLOW_MS);
    return () => {
      clearTimeout(first);
      clearInterval(t);
    };
  }, [enabled, refresh, preparing, mode]);

  return { status, error, refresh };
}
