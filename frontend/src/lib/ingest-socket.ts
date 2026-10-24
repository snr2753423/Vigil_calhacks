// A reconnecting WebSocket to /ws/ingest for one live device (Polar band or iPhone bed sensor).

import { liveSocketUrl, reconnectDelayMs } from "@/lib/live";

export type IngestStatus = "connecting" | "open" | "closed";

export type IngestReply = { type: string; [key: string]: unknown };

type Options = {
  device: "polar" | "phone";
  residentId: number;
  onStatus?: (s: IngestStatus) => void;
  onMessage?: (msg: IngestReply) => void;
};

/** Opens on construction, sends `hello` on every (re)connect, reconnects with backoff until `close()`.
 *  `send` drops messages while disconnected: live readings are only worth sending live. */
export class IngestSocket {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private stopped = false;
  status: IngestStatus = "connecting";

  constructor(private opts: Options) {
    this.connect();
  }

  private setStatus(s: IngestStatus) {
    this.status = s;
    this.opts.onStatus?.(s);
  }

  private connect() {
    this.setStatus("connecting");
    const ws = new WebSocket(liveSocketUrl(window.location, "/ws/ingest"));
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.setStatus("open");
      ws.send(JSON.stringify({ type: "hello", device: this.opts.device, resident_id: this.opts.residentId }));
    };
    ws.onmessage = (e: MessageEvent<string>) => {
      try {
        this.opts.onMessage?.(JSON.parse(e.data) as IngestReply);
      } catch {
        // ignore malformed frames
      }
    };
    ws.onerror = () => ws.close();
    ws.onclose = () => {
      if (this.stopped || this.ws !== ws) return;
      this.setStatus("closed");
      this.timer = setTimeout(() => this.connect(), reconnectDelayMs(this.attempt++));
    };
  }

  send(msg: object): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify({ resident_id: this.opts.residentId, ...msg }));
    return true;
  }

  close() {
    this.stopped = true;
    clearTimeout(this.timer);
    this.ws?.close();
    this.ws = null;
    this.setStatus("closed");
  }
}
