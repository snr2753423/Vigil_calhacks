"use client";

import { useEffect } from "react";

import { useLiveSocket } from "@/hooks/use-live-socket";
import { getJSON } from "@/lib/api";
import { unlockAudio } from "@/lib/sound";
import type { RosterEntry } from "@/lib/types";
import { usePrefs } from "@/store/prefs";
import { useLiveStore } from "@/store/live";

const ROSTER_REFRESH_MS = 5 * 60_000;

/** Mounted once in the root layout: the shared /ws/live connection, the roster, prefs and audio unlock. */
export function LiveSocket() {
  useLiveSocket();

  useEffect(() => {
    usePrefs.getState().hydrate();
    const onGesture = () => unlockAudio();
    window.addEventListener("pointerdown", onGesture);
    window.addEventListener("keydown", onGesture);
    return () => {
      window.removeEventListener("pointerdown", onGesture);
      window.removeEventListener("keydown", onGesture);
    };
  }, []);

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const rows = await getJSON<RosterEntry[]>("/api/residents");
        if (stop) return;
        useLiveStore.getState().setRoster(rows);
        timer = setTimeout(load, ROSTER_REFRESH_MS);
      } catch {
        if (!stop) timer = setTimeout(load, 5000); // backend or Tiger not up yet
      }
    };
    void load();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, []);

  return null;
}
