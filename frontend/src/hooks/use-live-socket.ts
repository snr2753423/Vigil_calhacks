"use client";

import { useEffect } from "react";

import { isStale, liveSocketUrl, parseLiveMessage, reconnectDelayMs } from "@/lib/live";
import { useLiveStore } from "@/store/live";

const WATCHDOG_MS = 1000;

/**
 * Keeps one /ws/live connection open (same origin via Caddy), reconnecting with backoff.
 * A watchdog replaces sockets that stay "open" but stop delivering heartbeats, since a
 * half-open connection may never fire `close`.
 */
export function useLiveSocket(): void {
  useEffect(() => {
    const { setStatus, receive } = useLiveStore.getState();
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let lastMessageAt = 0;
    let stopped = false;

    const scheduleReconnect = () => {
      setStatus("closed");
      retryTimer = setTimeout(connect, reconnectDelayMs(attempt++));
    };

    const connect = () => {
      setStatus("connecting");
      const socket = new WebSocket(liveSocketUrl(window.location));
      ws = socket;
      socket.onopen = () => {
        attempt = 0;
        lastMessageAt = Date.now();
        setStatus("open");
      };
      socket.onmessage = (event: MessageEvent<string>) => {
        lastMessageAt = Date.now();
        const msg = parseLiveMessage(event.data);
        if (msg) receive(msg, lastMessageAt);
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => {
        if (stopped || ws !== socket) return;
        scheduleReconnect();
      };
    };

    const watchdog = setInterval(() => {
      const socket = ws;
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      if (!isStale(lastMessageAt, Date.now(), useLiveStore.getState().heartbeatIntervalS)) return;
      ws = null; // detach: its late close event must not schedule a second reconnect
      socket.close();
      scheduleReconnect();
    }, WATCHDOG_MS);

    connect();
    return () => {
      stopped = true;
      clearInterval(watchdog);
      clearTimeout(retryTimer);
      ws?.close();
    };
  }, []);
}
