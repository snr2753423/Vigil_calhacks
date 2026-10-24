import type { Metadata, Viewport } from "next";
import { Atkinson_Hyperlegible } from "next/font/google";

import { AppHeader } from "@/components/app-header";
import { AlertToasts } from "@/components/alert-toasts";
import { DemoShortcuts } from "@/components/demo-shortcuts";
import { LiveSocket } from "@/components/live-socket";
import { ReplayBanner } from "@/components/replay/replay-banner";
import { ThemeSync } from "@/components/theme-sync";
import { THEME_BOOT_SCRIPT } from "@/lib/theme";
import "./globals.css";

// Atkinson Hyperlegible: designed for low-vision readers; two weights only.
const sans = Atkinson_Hyperlegible({
  variable: "--font-sans",
  subsets: ["latin"],
  weight: ["400", "700"],
});

export const metadata: Metadata = {
  title: "Vigil · Sunrise Gardens",
  description: "Night-shift decision support: who to check next and why.",
};

export const viewport: Viewport = {
  themeColor: "#1c1916",
};

// Warm night theme by default (low blue light); it follows the facility clock and a per-device Day/Night toggle.
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" data-theme="night" className={`${sans.variable} h-full antialiased`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body className="flex min-h-full flex-col">
        <LiveSocket />
        <ThemeSync />
        <AppHeader />
        <ReplayBanner />
        {children}
        <AlertToasts />
        <DemoShortcuts />
      </body>
    </html>
  );
}
