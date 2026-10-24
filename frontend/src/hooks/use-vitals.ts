"use client";

import { useEffect, useState } from "react";

import { useReplayCursor } from "@/hooks/use-clock";
import { getJSON } from "@/lib/api";
import type { AnomalyBucket, AnomalyResponse, Forecast, ForecastResponse, VitalsResponse } from "@/lib/types";

type Options = {
  residentId: number;
  metrics: string;
  spanMs: number;
  res: VitalsResponse["res"];
  /** Replay: reload as the cursor crosses each step of this much replay time. */
  stepMs: number;
  /** Live: reload this often. */
  refreshMs: number;
  /** Also load the Snowflake FORECAST runs of the last 2 h. */
  forecasts?: boolean;
  /** Changes force a reload (e.g. a new forecast arrived on /ws/live). */
  reloadKey?: unknown;
};

/** A resident's vitals window from Tiger's continuous aggregates plus the cached Snowflake overlays (anomaly
 *  buckets, optionally forecasts). Live: ends now (no `to`, so a fast device clock can't ask for the future) and
 *  reloads every `refreshMs`. Replay: ends at the cursor and reloads per `stepMs` of replay time. The overlays are
 *  optional: the charts never wait on them or fail because of them. */
export function useVitalsWindow({ residentId, metrics, spanMs, res, stepMs, refreshMs, forecasts = false, reloadKey }: Options) {
  const cursor = useReplayCursor();
  const replayEnd = cursor === null ? null : Math.ceil(cursor / stepMs) * stepMs;
  const [data, setData] = useState<VitalsResponse | null>(null);
  const [fetchedAt, setFetchedAt] = useState(0);
  const [anomalies, setAnomalies] = useState<AnomalyBucket[]>([]);
  const [forecastItems, setForecastItems] = useState<Forecast[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const ctrl = new AbortController();
    const load = async () => {
      setLoading(true);
      const end = replayEnd ?? Date.now();
      const from = new Date(end - spanMs).toISOString();
      const to = replayEnd === null ? null : new Date(Math.min(replayEnd, cursor ?? replayEnd)).toISOString();
      const q = new URLSearchParams({ metrics, from, res });
      const aq = new URLSearchParams({ kind: "anomaly", from });
      const fq = new URLSearchParams({ kind: "forecast", from: new Date(end - 2 * 3600_000).toISOString() });
      if (to) for (const p of [q, aq, fq]) p.set("to", to);
      const overlay = getJSON<AnomalyResponse>(`/api/insights/${residentId}?${aq}`, ctrl.signal)
        .then((r) => r.items)
        .catch(() => null);
      const fcs = forecasts
        ? getJSON<ForecastResponse>(`/api/insights/${residentId}?${fq}`, ctrl.signal)
            .then((r) => r.items)
            .catch(() => null)
        : Promise.resolve(null);
      try {
        const asked = Date.now();
        setData(await getJSON<VitalsResponse>(`/api/residents/${residentId}/vitals?${q}`, ctrl.signal));
        setFetchedAt(asked);
        setError(null);
      } catch (e) {
        if (!ctrl.signal.aborted) setError(e instanceof Error ? e.message : "Could not load vitals");
      } finally {
        if (!ctrl.signal.aborted) setLoading(false);
      }
      const items = await overlay;
      if (!ctrl.signal.aborted && items) setAnomalies(items);
      const f = await fcs;
      if (!ctrl.signal.aborted && f) setForecastItems(f);
    };
    void load();
    const t = replayEnd === null ? setInterval(load, refreshMs) : undefined;
    return () => {
      clearInterval(t);
      ctrl.abort();
    };
    // `cursor` is read only for the replay's `to`; `replayEnd` (its rounded value) drives reloads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [residentId, metrics, spanMs, res, replayEnd, refreshMs, forecasts, reloadKey]);

  return { data, fetchedAt, anomalies, forecasts: forecastItems, error, loading, cursor, replayEnd };
}
