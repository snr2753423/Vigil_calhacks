// Records the Vigil demo as separate scenes of JPEG frames (CDP screencast), for edit.sh to assemble.
// Usage: node record.mjs [scene ...]   (default: all scenes)   Needs `make dev` running on :8080.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.VIGIL_URL ?? "http://localhost:8080";
const OUT = path.resolve("out/frames");
const VIEW = { width: 1920, height: 1167 }; // headless Chrome captures 87 px less than the viewport → 1920×1080 frames. // pages are zoomed 4/3 (CSS) so the layout matches a 1440×810 screen

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const api = (p, body) =>
  fetch(BASE + p, {
    method: body === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((r) => r.json());

// A visible pointer + click ripple (headless browsers don't draw one).
const CURSOR = `
(() => {
  if (window.__cursor) return; window.__cursor = 1;
  const add = () => {
    const c = document.createElement('div');
    c.id = '__demo_cursor';
    c.style.cssText = 'position:fixed;left:0;top:0;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;' +
      'background:rgba(255,255,255,.85);border:2px solid #111;box-shadow:0 0 0 4px rgba(255,255,255,.18);' +
      'z-index:2147483647;pointer-events:none;transition:transform .12s;transform:translate(-100px,-100px)';
    document.documentElement.appendChild(c);
    let x = -100, y = -100;
    addEventListener('mousemove', (e) => { x = e.clientX; y = e.clientY; c.style.transform = 'translate(' + x + 'px,' + y + 'px)'; }, true);
    addEventListener('mousedown', () => {
      const r = document.createElement('div');
      r.style.cssText = 'position:fixed;left:' + x + 'px;top:' + y + 'px;width:16px;height:16px;margin:-8px 0 0 -8px;border-radius:50%;' +
        'border:3px solid #fbbf24;z-index:2147483646;pointer-events:none;transition:all .45s ease-out;opacity:1';
      document.documentElement.appendChild(r);
      requestAnimationFrame(() => { r.style.width = r.style.height = '56px'; r.style.margin = '-28px 0 0 -28px'; r.style.opacity = '0'; });
      setTimeout(() => r.remove(), 600);
    }, true);
  };
  const css = () => { const st = document.createElement('style');
    st.textContent = 'html{zoom:1.3333} nextjs-portal{display:none!important}';
    document.documentElement.appendChild(st); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => { css(); add(); }); else { css(); add(); }
})();`;

class Recorder {
  constructor(page) { this.page = page; }
  async start(name) {
    this.dir = path.join(OUT, name);
    fs.rmSync(this.dir, { recursive: true, force: true });
    fs.mkdirSync(this.dir, { recursive: true });
    this.frames = [];
    this.cdp = await this.page.context().newCDPSession(this.page);
    this.cdp.on("Page.screencastFrame", async (f) => {
      const file = path.join(this.dir, String(this.frames.length).padStart(6, "0") + ".jpg");
      fs.writeFileSync(file, Buffer.from(f.data, "base64"));
      this.frames.push({ file: path.basename(file), t: f.metadata.timestamp });
      this.cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }).catch(() => {});
    });
    await this.cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: 1920, maxHeight: 1400 });
    this.t0 = Date.now();
  }
  async stop() {
    await sleep(400);
    await this.cdp.send("Page.stopScreencast").catch(() => {});
    await this.cdp.detach().catch(() => {});
    // ffmpeg concat list with each frame's real duration (screencast only sends frames when the page changes).
    const lines = ["ffconcat version 1.0"];
    const end = Date.now() / 1000;
    this.frames.forEach((f, i) => {
      const next = i + 1 < this.frames.length ? this.frames[i + 1].t : Math.max(f.t + 0.04, end);
      lines.push(`file '${f.file}'`, `duration ${Math.max(0.001, next - f.t).toFixed(4)}`);
    });
    if (this.frames.length) lines.push(`file '${this.frames.at(-1).file}'`);
    fs.writeFileSync(path.join(this.dir, "list.txt"), lines.join("\n") + "\n");
    console.log(`  ${path.basename(this.dir)}: ${this.frames.length} frames, ${((Date.now() - this.t0) / 1000).toFixed(1)} s`);
  }
}

// Smooth pointer movement, then click.
async function moveTo(page, locator, { click = false, pause = 500 } = {}) {
  const box = await locator.boundingBox();
  if (!box) throw new Error("no box for " + locator);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 28 });
  await sleep(pause);
  if (click) { await page.mouse.down(); await page.mouse.up(); }
}
async function key(page, k, wait = 0) {
  await page.keyboard.press(k);
  if (wait) await sleep(wait);
}
async function gotoQueue(page) {
  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await page.getByText("Who to check next", { exact: false }).first().waitFor();
  await sleep(1500);
}
async function scrollBy(page, dy, ms = 1600) {
  const steps = 24;
  for (let i = 0; i < steps; i++) { await page.mouse.wheel(0, dy / steps); await sleep(ms / steps); }
}
async function openResidentFromQueue(page, id) {
  const card = page.locator(`a[href="/resident/${id}"]`, { hasText: "Open" }).first();
  const target = (await card.count()) ? card : page.locator(`a[href="/resident/${id}"]`).first();
  await target.scrollIntoViewIfNeeded();
  await moveTo(page, target, { click: true, pause: 700 });
  await page.waitForURL(`**/resident/${id}`);
  await page.waitForLoadState("networkidle").catch(() => {});
  await sleep(2500);
}

const SCENES = {
  // Live queue: the problem + what Vigil does.
  async s1_live(page, rec) {
    await gotoQueue(page);
    await key(page, "3", 5000);          // open last night's replay at 02:14 (paused): a busy queue
    await rec.start("s1_live");
    await sleep(2500);
    const grid = page.locator('a[href^="/resident/3"]').first();
    if (await grid.count()) await moveTo(page, grid, { pause: 1500 });
    await page.mouse.move(1100, 300, { steps: 40 }); await sleep(2500);
    await page.mouse.move(1250, 550, { steps: 40 }); await sleep(2500);
    const firstCard = page.locator('a[href^="/resident/"]', { hasText: "Open" }).first();
    if (await firstCard.count()) {
      const box = await firstCard.boundingBox();
      await page.mouse.move(box.x - 300, box.y - 60, { steps: 40 }); await sleep(3000);
      await page.mouse.move(box.x - 150, box.y - 20, { steps: 30 }); await sleep(3000);
    }
    await sleep(4000);
    await rec.stop();
  },

  // Replay: Alvarez's silent decline, her page, the Cortex card, the confusion note.
  async s2_alvarez(page, rec) {
    await gotoQueue(page);
    await rec.start("s2_alvarez");
    await sleep(1500);
    await key(page, "1", 6000);          // 23:12 first deviation
    await key(page, "3", 6000);          // 02:14 #1
    await openResidentFromQueue(page, 17);
    await page.mouse.move(700, 450, { steps: 40 }); await sleep(2500);
    await page.mouse.move(1000, 520, { steps: 30 }); await sleep(2500);
    const cites = page.getByRole("button", { name: /Care-plan citations/ }).first();
    if (await cites.count()) {
      await cites.scrollIntoViewIfNeeded(); await sleep(1200);
      await moveTo(page, cites, { click: true, pause: 600 }); await sleep(4000);
    }
    await scrollBy(page, 500, 2500); await sleep(2500);
    await scrollBy(page, -900, 2000); await sleep(1000);
    await key(page, "4", 7000);          // 02:20 confusion noted
    await scrollBy(page, 400, 1800); await sleep(3000);
    await rec.stop();
  },

  // Lee's forecast, then Ortiz's fall at 04:05.
  async s3_lee_ortiz(page, rec) {
    await gotoQueue(page);
    await rec.start("s3_lee_ortiz");
    await key(page, "5", 4000);          // 02:30 Lee forecast warning
    await openResidentFromQueue(page, 42);
    await page.mouse.move(800, 380, { steps: 40 }); await sleep(3500);
    await scrollBy(page, 350, 1500); await sleep(3500);
    await page.goto(BASE + "/", { waitUntil: "networkidle" }); await sleep(2000);
    await key(page, "7", 3000);          // 04:04, paused
    await key(page, " ");                 // play at 60×
    await sleep(9000);
    await key(page, " ", 3000);           // hold on the fall
    await rec.stop();
  },

  // Live: R-120 (presenter) out of bed at night → #1 → care-plan card.
  async s4_live_r120(page, rec) {
    await gotoQueue(page);
    await key(page, "l", 2500);
    await rec.start("s4_live_r120");
    await sleep(1500);
    await key(page, "n", 3500);           // night mode on
    await key(page, "b", 8000);           // bed exit for R-120
    const card = page.locator('a[href="/resident/120"]', { hasText: "Open" }).first();
    if (await card.count()) { await moveTo(page, card, { pause: 1500 }); }
    await key(page, "g"); await key(page, "r");
    await page.waitForURL("**/resident/120"); await sleep(3000);
    await page.mouse.move(900, 420, { steps: 30 });
    await page.getByText("Snowflake Cortex ·", { exact: false }).first().waitFor({ timeout: 45000 }).catch(() => {});
    await sleep(3000);
    const cites = page.getByRole("button", { name: /Care-plan citations/ }).first();
    if (await cites.count()) { await cites.scrollIntoViewIfNeeded(); await moveTo(page, cites, { click: true, pause: 500 }); await sleep(4500); }
    await rec.stop();
  },

  // Ask the facility (Cortex Analyst) + shift handoff (AI_AGG).
  async s5_ask_handoff(page, rec) {
    await page.goto(BASE + "/ask", { waitUntil: "networkidle" }); await sleep(1500);
    await rec.start("s5_ask_handoff");
    await sleep(1500);
    const chip = page.getByRole("button", { name: /more than 3 bed exits/ }).first();
    await moveTo(page, chip, { click: true, pause: 700 });
    await page.getByText("SQL from Cortex Analyst").first().waitFor({ timeout: 60000 });
    await sleep(3000);
    await scrollBy(page, 450, 2000); await sleep(2500);
    const sql = page.getByText("SQL from Cortex Analyst").first();
    await sql.scrollIntoViewIfNeeded(); await moveTo(page, sql, { click: true, pause: 500 }); await sleep(4000);
    await page.goto(BASE + "/handoff", { waitUntil: "networkidle" }); await sleep(2000);
    const gen = page.getByRole("button", { name: /^Generate/ }).first();
    await moveTo(page, gen, { click: true, pause: 600 });
    await page.getByText(/SBAR/i).first().waitFor({ timeout: 90000 }).catch(() => {});
    await sleep(3000);
    await scrollBy(page, 600, 3000); await sleep(2500);
    await scrollBy(page, 600, 3000); await sleep(2500);
    await rec.stop();
  },

  // Under the hood: benchmark + Tiger / Snowflake panels + outcome.
  async s6_under_the_hood(page, rec) {
    await page.goto(BASE + "/under-the-hood", { waitUntil: "networkidle" }); await sleep(3000);
    await rec.start("s6_under_the_hood");
    await sleep(2000);
    const bench = page.getByRole("button", { name: /Run benchmark/ }).first();
    if (await bench.count()) {
      await bench.scrollIntoViewIfNeeded(); await sleep(800);
      await moveTo(page, bench, { click: true, pause: 600 });
      await sleep(12000);
    }
    await scrollBy(page, 600, 3000); await sleep(3000);
    await scrollBy(page, 600, 3000); await sleep(3000);
    await scrollBy(page, 700, 3000); await sleep(3500);
    await scrollBy(page, 700, 3000); await sleep(3500);
    await rec.stop();
  },
};

const wanted = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(SCENES);
const browser = await chromium.launch({ channel: "chrome", headless: true }); // the installed Google Chrome
const context = await browser.newContext({ viewport: VIEW, deviceScaleFactor: 1, colorScheme: "dark" });
await context.addInitScript(CURSOR);
await context.addInitScript(() => {
  localStorage.setItem("vigil.prefs", JSON.stringify({ role: "rn", sound: false }));
});
const page = await context.newPage();
const rec = new Recorder(page);
try {
  await api("/api/sim/simulated_band", { enabled: true });
  for (const name of wanted) {
    console.log("recording", name);
    await SCENES[name](page, rec);
  }
} finally {
  // Leave the app as we found it: live, night off, R-120 back in bed, simulated band off.
  await api("/api/replay/stop", {}).catch(() => {});
  await api("/api/sim/force_night", { enabled: false }).catch(() => {});
  await api("/api/sim/simulated_band", { enabled: false }).catch(() => {});
  await browser.close();
}
