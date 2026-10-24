"use client";

import { useEffect } from "react";

import { useTheme } from "@/hooks/use-theme";
import { usePrefs } from "@/store/prefs";

/** Keeps <html data-theme> in step with the facility clock and the header's Day/Night toggle. */
export function ThemeSync() {
  const { theme } = useTheme();
  const hydrated = usePrefs((s) => s.hydrated); // before that, the boot script's pick stands
  useEffect(() => {
    if (!hydrated) return;
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "night" ? "#1c1916" : "#f6f1e9");
  }, [theme, hydrated]);
  return null;
}
