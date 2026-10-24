"use client";

import { type RefObject, useLayoutEffect, useRef } from "react";

const DURATION_MS = 450;

/**
 * FLIP reordering: children marked `data-flip="<key>"` glide from their previous position to the new one
 * whenever `version` changes. Positions are re-recorded after every render, so a card that grew (an open
 * form) doesn't leave stale offsets. Uses offsetTop, so page scrolling doesn't animate anything.
 */
export function useFlip(container: RefObject<HTMLElement | null>, version: unknown): void {
  const prev = useRef(new Map<string, number>());
  const lastVersion = useRef(version);

  useLayoutEffect(() => {
    const root = container.current;
    if (!root) return;
    const reordered = lastVersion.current !== version;
    lastVersion.current = version;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const next = new Map<string, number>();
    root.querySelectorAll<HTMLElement>("[data-flip]").forEach((node) => {
      const key = node.dataset.flip as string;
      const top = node.offsetTop;
      next.set(key, top);
      const before = prev.current.get(key);
      if (!reordered || reduce || before === undefined) return;
      const dy = before - top;
      if (Math.abs(dy) < 2) return;
      node.animate([{ transform: `translateY(${dy}px)` }, { transform: "translateY(0)" }], {
        duration: DURATION_MS,
        easing: "cubic-bezier(0.2, 0.8, 0.2, 1)",
      });
    });
    prev.current = next;
  });
}
