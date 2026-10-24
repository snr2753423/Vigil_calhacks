"use client";

import { useEffect } from "react";

import { liveSocketUrl, parseLiveMessage, reconnectDelayMs } from "@/lib/live";
import { useLiveStore } from "@/store/live";

/** Keeps one /ws/live connection open (same origin via Caddy), reconnecting with backoff. */
export function useLiveSocket(): void {
  useEffect(() => {
    const { setStatus, receive } = useLiveStore.getState();
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let stopped = false;

    const connect = () => {
      setStatus("connecting");
      ws = new WebSocket(liveSocketUrl(window.location));
      ws.onopen = () => {
        attempt = 0;
        setStatus("open");
      };
      ws.onmessage = (event: MessageEvent<string>) => {
        const msg = parseLiveMessage(event.data);
        if (msg) receive(msg, Date.now());
      };
      ws.onerror = () => ws?.close();
      ws.onclose = () => {
        if (stopped) return;
        setStatus("closed");
        retryTimer = setTimeout(connect, reconnectDelayMs(attempt++));
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(retryTimer);
      ws?.close();
    };
  }, []);
}
