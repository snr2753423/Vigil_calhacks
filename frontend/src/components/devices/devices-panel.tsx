"use client";

import { Bluetooth, BluetoothOff, Copy, Heart, Loader2, Smartphone, TriangleAlert } from "lucide-react";
import QRCode from "qrcode";
import { useCallback, useEffect, useState } from "react";
import { cn } from "cn";

import { BigButton, Panel } from "@/components/admin/admin-panel";
import { useNow } from "@/hooks/use-now";
import { getJSON } from "@/lib/api";
import type { DevicesResponse } from "@/lib/types";
import { useLiveStore } from "@/store/live";
import { bluetooth, usePolar, type PolarStatus } from "@/store/polar";

const POLL_MS = 3000;

/** Live hardware (SPEC §11.5): the Polar band over Web Bluetooth, the iPhone bed sensor's QR code, and the
 * device list. */
export function DevicesPanel() {
  const [info, setInfo] = useState<DevicesResponse | null>(null);

  const refresh = useCallback(async () => {
    try {
      setInfo(await getJSON<DevicesResponse>("/api/devices"));
    } catch {
      // backend restarting: the next poll retries
    }
  }, []);

  useEffect(() => {
    const first = setTimeout(refresh, 0);
    const t = setInterval(refresh, POLL_MS);
    return () => {
      clearTimeout(first);
      clearInterval(t);
    };
  }, [refresh]);

  const rid = info?.live_resident_id ?? 120;
  const resident = useLiveStore((s) => s.roster[rid]);
  const who = resident ? `R-${rid} · ${resident.name} (room ${resident.room})` : `R-${rid}`;

  return (
    <main className="mx-auto w-full max-w-[80rem] flex-1 space-y-4 px-4 pt-6 pb-16 sm:px-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">Devices · live hardware</h1>
        <p className="text-base text-muted-foreground">
          The presenter is {who}: a Polar Verity Sense on the wrist and an iPhone under the pillow stream into Tiger
          every second.
        </p>
      </div>
      {info?.simulated_band && (
        <p role="alert" className="flex items-start gap-2 rounded-xl bg-high-bg px-4 py-3 text-base text-high ring-1 ring-high-solid">
          <TriangleAlert className="mt-0.5 size-5 shrink-0" aria-hidden />
          The simulated band for R-{rid} is on (Admin), so readings from the real devices are ignored. Turn it off in
          Admin to use the band and the bed sensor.
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <PolarCard residentId={rid} who={who} />
        <BedCard info={info} residentId={rid} />
      </div>
      <DeviceList info={info} />
    </main>
  );
}

const POLAR_LABEL: Record<PolarStatus, string> = {
  unsupported: "Web Bluetooth not available",
  idle: "Not connected",
  choosing: "Choose the band in Chrome's list…",
  connecting: "Connecting…",
  connected: "Connected",
  reconnecting: "Lost the band — reconnecting…",
  error: "Error",
};

function PolarCard({ residentId, who }: { residentId: number; who: string }) {
  const p = usePolar();
  const now = useNow();
  const [supported, setSupported] = useState<boolean | null>(null);
  useEffect(() => {
    const t = setTimeout(() => setSupported(!!bluetooth() && window.isSecureContext), 0);
    return () => clearTimeout(t);
  }, []);
  const active = p.status === "connected" || p.status === "connecting" || p.status === "reconnecting";
  const age = p.lastAt !== null && now !== null ? Math.max(0, (now - p.lastAt) / 1000) : null;
  const stale = age !== null && age > 5;

  return (
    <Panel title="Polar heart-rate band">
      <div className="flex flex-wrap items-center gap-3">
        {active ? (
          <BigButton icon={BluetoothOff} onClick={() => p.disconnect()}>
            Disconnect
          </BigButton>
        ) : (
          <BigButton
            icon={Bluetooth}
            tone="teal"
            disabled={supported === false || p.status === "choosing"}
            onClick={() => void p.connect(residentId)}
          >
            {p.status === "choosing" ? "Choosing…" : "Connect Polar"}
          </BigButton>
        )}
        <span
          role="status"
          className={cn(
            "inline-flex h-9 items-center gap-2 rounded-full px-3 text-sm ring-1",
            p.status === "connected" && !stale
              ? "bg-surface-2 text-accent ring-accent"
              : p.status === "reconnecting" || stale
                ? "bg-high-bg text-high ring-high-solid"
                : "bg-surface-2 text-muted-foreground ring-line",
          )}
        >
          {(p.status === "connecting" || p.status === "reconnecting") && <Loader2 className="size-4 animate-spin" aria-hidden />}
          {supported === false ? POLAR_LABEL.unsupported : POLAR_LABEL[p.status]}
          {p.deviceName && active ? ` · ${p.deviceName}` : ""}
        </span>
      </div>

      <div className="mt-4 flex items-end gap-4">
        <Heart
          className={cn("mb-3 size-12 shrink-0", p.hr !== null && !stale ? "animate-pulse fill-accent text-accent" : "text-foreground")}
          aria-hidden
        />
        <p className="text-8xl font-bold leading-none tabular-nums" aria-live="polite" aria-label="Heart rate">
          {p.hr ?? "—"}
        </p>
        <p className="mb-2 text-2xl text-muted-foreground">bpm</p>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-base sm:grid-cols-3">
        <Item label="Skin contact" value={p.worn === null ? "—" : p.worn ? "On the wrist" : "Off the wrist"} />
        <Item label="Last reading" value={age === null ? "—" : age < 1.5 ? "just now" : `${age.toFixed(0)} s ago`} />
        <Item label="Streamed" value={`${p.sent} / ${p.readings}`} />
        <Item label="Assigned to" value={who} />
        <Item label="Ingest socket" value={p.ingest} />
      </dl>
      {p.rejected && <Warn>{p.rejected}</Warn>}
      {p.error && p.status !== "connected" && <Warn>{p.error}</Warn>}
      {supported === false && (
        <Warn>
          Use Chrome (or Edge) on the Mac over http://localhost:8080 or the HTTPS tunnel. Safari and iPhone browsers
          don&apos;t support Web Bluetooth.
        </Warn>
      )}
      <p className="mt-3 text-sm text-muted-foreground">
        Put the band in heart-rate mode (green light on, skin contact). No macOS pairing needed; Chrome needs Bluetooth
        permission in System Settings → Privacy &amp; Security → Bluetooth. It reconnects on its own if the band drops,
        and keeps streaming while you open other pages (a full page reload needs a new click).
      </p>
    </Panel>
  );
}

function BedCard({ info, residentId }: { info: DevicesResponse | null; residentId: number }) {
  const [origin, setOrigin] = useState<string | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const phone = useLiveStore((s) => s.devices[`${residentId}:phone`]);
  const now = useNow();

  useEffect(() => {
    const t = setTimeout(() => setOrigin(window.location.protocol === "https:" ? window.location.origin : null), 0);
    return () => clearTimeout(t);
  }, []);

  // PUBLIC_URL (the tunnel) wins; a page opened over HTTPS can fall back to its own origin.
  const url = info?.bed_url ?? (origin ? `${origin}/bed/${residentId}` : null);
  useEffect(() => {
    let stop = false;
    if (!url) return;
    QRCode.toDataURL(url, { margin: 1, width: 480, errorCorrectionLevel: "M", color: { dark: "#1c1916", light: "#ffffff" } })
      .then((d) => !stop && setQr(d))
      .catch(() => !stop && setQr(null));
    return () => {
      stop = true;
    };
  }, [url]);

  const bed = info?.bed?.[String(residentId)];
  const cal = bed?.calibration;
  const age = phone && now !== null ? Math.max(0, (now - phone.receivedAt) / 1000) : null;
  const live = age !== null && age < 5;

  return (
    <Panel title="iPhone bed sensor">
      <div className="flex flex-col gap-4 sm:flex-row">
        <div className="flex size-52 shrink-0 items-center justify-center rounded-xl bg-surface-2 p-2">
          {url && qr ? (
            // eslint-disable-next-line @next/next/no-img-element -- a data: URL generated in the browser
            <img src={qr} alt={`QR code for ${url}`} className="size-full" />
          ) : (
            <p className="p-3 text-center text-sm text-muted-foreground">Set PUBLIC_URL in .env to the tunnel&apos;s https URL</p>
          )}
        </div>
        <div className="min-w-0 space-y-2 text-base">
          <p className="text-muted-foreground">Scan with the iPhone camera, then tap Start sensor on the phone.</p>
          {url ? (
            <div className="flex items-center gap-2">
              <code className="min-w-0 break-all rounded bg-surface-2 px-2 py-1 text-sm">{url}</code>
              <button
                type="button"
                aria-label="Copy URL"
                onClick={() => {
                  void navigator.clipboard?.writeText(url).then(() => setCopied(true));
                  setTimeout(() => setCopied(false), 1500);
                }}
                className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-line hover:bg-surface-2"
              >
                <Copy className="size-4" aria-hidden />
              </button>
              {copied && <span className="text-sm text-accent">Copied</span>}
            </div>
          ) : (
            <Warn>
              No HTTPS URL yet: run <code>make tunnel</code>, put its https://….trycloudflare.com address in .env as
              PUBLIC_URL (no restart needed), or open this page through the tunnel.
            </Warn>
          )}
          {!info?.bed_url && origin && <p className="text-sm text-muted-foreground">Using this page&apos;s own HTTPS address (PUBLIC_URL is not set).</p>}
        </div>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-base sm:grid-cols-3">
        <Item
          label="State"
          value={live && phone?.state ? phone.state : bed ? `${bed.in_bed ? "in bed" : "out of bed"} (phone not streaming)` : "—"}
          icon={<Smartphone className={cn("size-4", live ? "text-accent" : "text-foreground")} aria-hidden />}
        />
        <Item label="Movement" value={live && phone?.bed_motion !== undefined ? `${phone.bed_motion.toFixed(1)} / 10` : "—"} />
        <Item label="Last summary" value={age === null ? "—" : age < 1.5 ? "just now" : `${age.toFixed(0)} s ago`} />
        <Item
          label="Calibration"
          value={!cal ? "—" : cal.calibrated ? `done · ${MODE_LABEL[cal.mode]}` : "not calibrated (defaults)"}
        />
      </dl>
      {cal?.warnings?.map((w) => <Warn key={w}>{w}</Warn>)}
    </Panel>
  );
}

export const MODE_LABEL = {
  tilt: "tilt (exit in ~2 s)",
  energy: "breathing motion (exit in ~4 s)",
  burst: "burst + 10 s still",
} as const;

function DeviceList({ info }: { info: DevicesResponse | null }) {
  const roster = useLiveStore((s) => s.roster);
  const rows = info?.devices ?? [];
  return (
    <Panel title="Device list">
      {rows.length === 0 ? (
        <p className="text-base text-muted-foreground">No device has connected since the backend started.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[32rem] text-left text-base">
            <thead className="text-sm text-muted-foreground">
              <tr>
                <th className="py-1 pr-3 ">Device</th>
                <th className="py-1 pr-3 ">Resident</th>
                <th className="py-1 pr-3 ">Status</th>
                <th className="py-1 pr-3 ">Last reading</th>
                <th className="py-1 ">Readings</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={`${d.kind}-${d.resident_id}`} className="border-t border-line">
                  <td className="py-2 pr-3">{d.kind === "polar" ? "Polar band (wrist)" : "iPhone (bed sensor)"}</td>
                  <td className="py-2 pr-3">
                    R-{d.resident_id} {roster[d.resident_id]?.name ?? ""}
                  </td>
                  <td className="py-2 pr-3">
                    <span className={cn("", d.online ? "text-accent" : "text-muted-foreground")}>
                      {d.online ? "● Online" : d.sockets > 0 ? "○ Connected, silent" : "○ Offline"}
                    </span>
                  </td>
                  <td className="py-2 pr-3">{d.age_s === null ? "—" : `${d.age_s.toFixed(0)} s ago`}</td>
                  <td className="py-2 tabular-nums">{d.readings}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {info && (
        <p className="mt-3 text-sm text-muted-foreground">
          Tiger writer: {info.stats.phase} · {info.stats.rows_written} rows, {info.stats.events_written} bed events written
          {info.stats.last_flush_ms !== null && ` · last batch ${info.stats.last_flush_ms} ms`}
          {info.stats.last_error && ` · last error: ${info.stats.last_error}`}
        </p>
      )}
    </Panel>
  );
}

function Item({ label, value, icon }: { label: string; value: string; icon?: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="flex items-center gap-1.5 break-words">
        {icon}
        {value}
      </dd>
    </div>
  );
}

function Warn({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-3 flex items-start gap-2 rounded-lg bg-high-bg px-3 py-2 text-sm text-high ring-1 ring-high-solid">
      <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden /> <span>{children}</span>
    </p>
  );
}
