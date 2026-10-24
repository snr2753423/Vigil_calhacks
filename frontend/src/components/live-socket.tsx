"use client";

import { useLiveSocket } from "@/hooks/use-live-socket";

/** Mounted once in the root layout so every page shares the /ws/live connection. */
export function LiveSocket() {
  useLiveSocket();
  return null;
}
