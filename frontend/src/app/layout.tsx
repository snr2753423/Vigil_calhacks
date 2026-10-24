import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { AppHeader } from "@/components/app-header";
import { AlertToasts } from "@/components/alert-toasts";
import { DemoShortcuts } from "@/components/demo-shortcuts";
import { LiveSocket } from "@/components/live-socket";
import { ReplayBanner } from "@/components/replay/replay-banner";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Vigil · Sunrise Gardens",
  description: "Night-shift decision support: who to check next and why.",
};

export const viewport: Viewport = {
  themeColor: "#0d1424",
  colorScheme: "dark",
};

// Dark "night shift" theme by default (SPEC §11): less glare on a dim unit.
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`dark ${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col">
        <LiveSocket />
        <AppHeader />
        <ReplayBanner />
        {children}
        <AlertToasts />
        <DemoShortcuts />
      </body>
    </html>
  );
}
