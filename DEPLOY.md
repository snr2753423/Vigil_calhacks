# Deploying Vigil to Vultr

This guide puts Vigil on a public HTTPS address such as `https://203.0.113.7.sslip.io`, so judges can open it and your iPhone can open the bed-sensor page without a tunnel. It assumes you have never used Vultr or SSH before. Run every command **on your Mac, from the repo folder** (`cd ~/Practicerun/vigil`), unless a step says otherwise.

How it works: one small Ubuntu server runs the same Docker Compose stack as `make up` (Caddy + backend + frontend). Caddy gets a real Let's Encrypt certificate for your address. The server talks to the same Tiger Cloud and Snowflake accounts as your laptop. Your secrets (`.env` and the Snowflake key) are copied over SSH. They never go through git.

**Steps you must do by hand** (the rest is one command each):

1. Create the Vultr account (step 1).
2. Add your SSH key to Vultr (step 2).
3. Click "Deploy" in Vultr (step 3).
4. Put two lines in `.env` (step 4).
5. Destroy the server when you are done (last section).

---

## 1. Vultr account (by hand, once)

1. Go to <https://www.vultr.com>, sign up, and add a payment method. Servers are billed **by the hour**, so a weekend costs about a dollar or two.

## 2. SSH key on your Mac (by hand, once)

An SSH key lets your Mac log in to the server without a password.

1. Check whether you already have one:
   ```bash
   ls ~/.ssh/id_ed25519.pub
   ```
   If it prints the path, skip to item 3.
2. If it says "No such file", create one. Press Enter at every question; an empty passphrase is fine for a hackathon:
   ```bash
   ssh-keygen -t ed25519 -C "vigil-vultr"
   ```
3. Copy the **public** key (the `.pub` file, never the other one) to the clipboard:
   ```bash
   pbcopy < ~/.ssh/id_ed25519.pub
   ```
4. In Vultr, go to **Account → SSH Keys → Add SSH Key**. Name it "mac", paste, and save.

## 3. Create the server (by hand)

In Vultr, click **Deploy +** → **Deploy New Server** and choose:

| Setting | Choose |
|---|---|
| Type | **Cloud Compute – Shared CPU** |
| Location | **Los Angeles** (or **Silicon Valley**): close to Berkeley, and to Tiger / Snowflake on the US West coast |
| Image | **Ubuntu 24.04 LTS x64** |
| Plan | At least **2 GB RAM** (e.g. 1 vCPU / 2 GB). **2 vCPU / 4 GB** makes builds about twice as fast and is worth it on demo day. |
| Additional features | Turn **off** Auto Backups (extra cost). IPv6 is optional. |
| SSH Keys | **Tick "mac"** (the key from step 2). This is what lets the scripts log in. |
| Server hostname / label | `vigil` |

Click **Deploy Now**. After about a minute the status shows **Running**. Copy the server's **IP address** (e.g. `203.0.113.7`) from the server page.

> You don't need a Vultr Firewall Group: step 5 turns on the server's own firewall (ports 22, 80 and 443). If you attach a Vultr Firewall Group anyway, it must allow TCP 22, 80 and 443, plus UDP 443.

## 4. Tell the scripts where the server is (by hand, once per server)

Open `.env` (in the repo root) and add two lines, using your IP:

```bash
DEPLOY_HOST=203.0.113.7
DOMAIN=203.0.113.7.sslip.io
```

`DOMAIN` is the **only** setting that names the public site. `<ip>.sslip.io` is a free DNS name that always points at that IP, so you don't need to buy a domain for the practice run. Caddy gets a real HTTPS certificate for it. The iPhone QR code (`PUBLIC_URL`) follows `DOMAIN` automatically on the server.

Check that you can log in, then log out. The first time, SSH asks whether you trust the server: type `yes`.

```bash
ssh root@203.0.113.7 'echo connected'
```

## 5. Prepare the server (one command, once per server)

```bash
scripts/deploy.sh setup
```

This takes about 2 minutes. It installs Docker, adds 4 GB of swap (a 2 GB server needs it to build the frontend), and turns on the firewall with SSH, HTTP and HTTPS allowed. It ends by printing the Docker version, memory and firewall rules.

## 6. Copy your secrets (one command; again whenever `.env` changes)

```bash
scripts/deploy.sh secrets
```

This copies `.env` and `secrets/snowflake_rsa_key.p8` to `/opt/vigil` on the server over SSH (encrypted), readable by root only. They are never committed. The code upload in the next step skips them on purpose.

## 7. Stop the laptop backend

The server and `make dev` share the same Tiger database. Only one backend may stream and raise alerts. The second one stands by, and the READY checklist warns about it. **Press Ctrl-C in the terminal running `make dev`** before you deploy, and keep it stopped while the server is live.

## 8. First deploy (one command)

```bash
scripts/deploy.sh push
```

This does three things:
1. Copies the code to the server. `.git`, `.env`, `secrets/`, `node_modules` and caches are excluded.
2. Builds the images on the server: about 5–8 minutes the first time on 2 GB, then 1–3 minutes.
3. Starts the stack and waits until `https://<DOMAIN>/api/health` answers. The first start also fetches the certificate (about 10–30 s).

It ends with:

```
✓ Live: https://203.0.113.7.sslip.io    (iPhone bed page: https://203.0.113.7.sslip.io/bed/120)
```

## 9. Get the demo to a known-good state

```bash
scripts/deploy.sh reset --simulated-band
```

This runs `backend/scripts/reset_demo.py` inside the backend container.
- It cleans test data, resets the demo controls, checks the anomaly models and the prepared replay (re-running only what's missing), and runs live Snowflake checks.
- It warms every page through `https://<DOMAIN>` and prints the **READY checklist**, which must be all PASS.
- Leave out `--simulated-band` once the Polar band and iPhone are working. Until then, it simulates R-120 so his card isn't a "no device data" device issue.

## 10. Check it end to end

1. On the Mac, open `https://<DOMAIN>` in **Chrome**. The Rounds queue appears, the header says **Live** and **Connected**, and the clock ticks.
2. Press **?** to open the demo shortcuts. Press **3**: the replay jumps to 02:14 with Alvarez **#1 HIGH**. Press **L** to go back to live.
3. Open **Under the hood**. Every panel shows numbers. Click the benchmark button.
4. **iPhone**: open **Devices** on the Mac. The QR code points at `https://<DOMAIN>/bed/120` (no tunnel needed). Scan it with the iPhone Camera, open it in Safari, tap **Start sensor**, then **Allow**. The page shows "● streaming".
5. **Polar band**: on **Devices** in Mac Chrome, click **Connect Polar**. Web Bluetooth works on the HTTPS address.

---

## Redeploy after a code change

Commit (or don't; the upload copies your working folder), then:

```bash
scripts/deploy.sh push
```

If you changed `.env`, run `scripts/deploy.sh secrets` first. A redeploy restarts the backend. Its shutdown cancels any Snowflake queries still running, so nothing piles up on the warehouse.

## Everyday commands

| Command | What it does |
|---|---|
| `scripts/deploy.sh status` | Containers, plus a one-line health summary (live loop, evaluator, sync) |
| `scripts/deploy.sh logs` | Follow all logs (Ctrl-C stops following; the app keeps running) |
| `scripts/deploy.sh logs backend` | Only the backend (also `frontend`, `caddy`) |
| `scripts/deploy.sh reset` | READY checklist on the server (`--simulated-band`, `--retrain`, `--no-clean`) |
| `scripts/deploy.sh ssh` | A shell on the server in `/opt/vigil` (`exit` to leave) |
| `scripts/deploy.sh stop` | Stop the containers. The server still bills until you destroy it. |

The `make` equivalents are `make deploy-setup`, `deploy-secrets`, `deploy`, `deploy-status`, `deploy-logs` and `deploy-reset`.

## Switch to a real domain (Cal Hacks)

1. At your domain registrar, add a DNS **A record**, e.g. `vigil` → your server's IP. Wait until this prints the IP (usually a few minutes):
   ```bash
   dig +short vigil.example.com
   ```
2. In `.env`, set `DOMAIN=vigil.example.com`.
3. Run:
   ```bash
   scripts/deploy.sh secrets && scripts/deploy.sh push
   ```
   Caddy gets a certificate for the new name, and the iPhone QR code follows it.

## Destroy the server when you're done (by hand)

1. *Optional:* stop the stack with `scripts/deploy.sh stop`.
2. In Vultr, open the server → **Server Destroy** (the trash-can icon) → confirm. Billing stops; a stopped server still bills.
3. Forget the server's SSH fingerprint, so a future server with the same IP doesn't trigger a warning:
   ```bash
   ssh-keygen -R 203.0.113.7
   ```
4. Remove `DEPLOY_HOST` and `DOMAIN` from `.env` (or update them for the next server).

Destroying the server deletes the copy of `.env` and the Snowflake key on it. Your data lives in Tiger Cloud and Snowflake, so nothing is lost.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Permission denied (publickey)` | The SSH key wasn't ticked when the server was created. Destroy it and re-create it with the key ticked (fastest), or add the key through the Vultr web console. |
| `WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED` | A new server reused an old IP: `ssh-keygen -R <ip>`, then retry. |
| `push` waits and then says https didn't answer | Run `scripts/deploy.sh logs caddy`. Look for "challenge failed" or "timeout": ports 80/443 are blocked (a Vultr Firewall Group?) or `DOMAIN` has a typo. Let's Encrypt rate limits on sslip.io are rare. If you hit one, use `DOMAIN=<ip>.nip.io` instead. |
| Build stops with `Killed` | Out of memory: run `scripts/deploy.sh setup` again (it adds swap), or resize to 4 GB in Vultr. |
| `502` right after a push | The backend is still starting (~20 s). Check `scripts/deploy.sh status`. |
| Status shows live loop `standby` | Another backend (your laptop's `make dev`) holds the Tiger lease. Stop it. The server takes over within 30 s. |
| READY: "Backend not reachable" | Run `scripts/deploy.sh logs backend`. A missing `.env` or key means you should run `scripts/deploy.sh secrets`, then `push`. |
| iPhone page says motion isn't allowed | Close the tab and scan again. iOS asks once per page load, and only after the tap on **Start sensor**. |

**Security notes.** The app has no login (SPEC: role switcher only), so anyone with the URL can see the synthetic data and use Admin. That's fine for a demo with synthetic data. Destroy the server afterwards. The Snowflake key on the server is readable only by root.
