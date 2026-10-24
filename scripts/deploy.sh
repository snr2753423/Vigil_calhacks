#!/usr/bin/env bash
# Vigil → a Vultr (or any Ubuntu) server, run from the repo root on your Mac. Step-by-step guide: DEPLOY.md.
#
#   scripts/deploy.sh setup            once per server: Docker, 4 GB swap, firewall (22, 80, 443)
#   scripts/deploy.sh secrets          copy .env + secrets/ to the server over SSH (never through git)
#   scripts/deploy.sh push             copy the code, rebuild, restart, wait until https://$DOMAIN answers
#   scripts/deploy.sh status           containers + the live health summary
#   scripts/deploy.sh logs [service]   follow logs (backend | frontend | caddy; all if omitted). Ctrl-C to stop.
#   scripts/deploy.sh reset [args]     run backend/scripts/reset_demo.py on the server (READY checklist)
#   scripts/deploy.sh ssh              open a shell on the server
#   scripts/deploy.sh stop             stop the stack (the server keeps running and billing: destroy it in Vultr)
#
# Reads two settings from .env: DEPLOY_HOST (the server's IP) and DOMAIN (e.g. 203.0.113.7.sslip.io).
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || { echo "No .env here. Run from the repo root after setting up .env (README → Setup)."; exit 1; }
env_get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' -e 's/[[:space:]]*#.*$//' || true; }

HOST="${DEPLOY_HOST:-$(env_get DEPLOY_HOST)}"
DOMAIN="${DOMAIN:-$(env_get DOMAIN)}"
USER_AT="${DEPLOY_USER:-root}@${HOST}"
REMOTE=/opt/vigil
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30)

need_host() {
  [ -n "$HOST" ] || { echo "Add DEPLOY_HOST=<your server's IP> to .env (DEPLOY.md step 4)."; exit 1; }
}
need_domain() {
  [ -n "$DOMAIN" ] || { echo "Add DOMAIN=${HOST:-<ip>}.sslip.io to .env (DEPLOY.md step 4)."; exit 1; }
}
remote() { ssh "${SSH_OPTS[@]}" "$USER_AT" "$@"; }
remote_tty() { ssh -t "${SSH_OPTS[@]}" "$USER_AT" "$@"; }

cmd="${1:-help}"
shift || true

case "$cmd" in
  setup)
    need_host
    echo "→ Preparing $USER_AT (Docker, swap, firewall). Takes ~2 min."
    remote 'bash -s' <<'EOS'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq rsync curl ca-certificates ufw >/dev/null
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh >/dev/null
fi
systemctl enable --now docker >/dev/null
# 2 GB of RAM is tight for `next build`: add 4 GB of swap once.
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
# SSH first, so enabling the firewall can never lock you out.
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow 443/udp >/dev/null
ufw --force enable >/dev/null
mkdir -p /opt/vigil/secrets && chmod 700 /opt/vigil/secrets
echo "docker:   $(docker --version)"
echo "compose:  $(docker compose version --short)"
echo "memory:   $(free -h | awk '/Mem:/ {print $2}') RAM + $(free -h | awk '/Swap:/ {print $2}') swap"
echo "firewall: $(ufw status | grep -c ALLOW) allow rules (22, 80, 443)"
EOS
    echo "✓ Server ready. Next: scripts/deploy.sh secrets"
    ;;

  secrets)
    need_host
    need_domain
    [ -f secrets/snowflake_rsa_key.p8 ] || { echo "secrets/snowflake_rsa_key.p8 not found (make keygen)."; exit 1; }
    echo "→ Copying .env and secrets/snowflake_rsa_key.p8 to $USER_AT:$REMOTE over SSH"
    remote "mkdir -p $REMOTE/secrets && chmod 700 $REMOTE/secrets"
    scp -q "${SSH_OPTS[@]}" .env "$USER_AT:$REMOTE/.env"
    scp -q "${SSH_OPTS[@]}" secrets/snowflake_rsa_key.p8 "$USER_AT:$REMOTE/secrets/snowflake_rsa_key.p8"
    remote "chmod 600 $REMOTE/.env $REMOTE/secrets/snowflake_rsa_key.p8 && ls -l $REMOTE/.env $REMOTE/secrets"
    echo "✓ Secrets on the server (readable by root only). Next: scripts/deploy.sh push"
    ;;

  push)
    need_host
    need_domain
    if lsof -nP -iTCP:8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
      echo "! A backend is running on this Mac (make dev). It shares Tiger with the server: only one of them streams"
      echo "  and alerts (the other stands by). Stop make dev (Ctrl-C) before the demo."
    fi
    echo "→ Copying the code to $USER_AT:$REMOTE (secrets, .env, node_modules, caches excluded)"
    rsync -az --delete -e "ssh ${SSH_OPTS[*]}" \
      --exclude .git/ --exclude .claude/ --exclude .DS_Store \
      --exclude .env --exclude '.env.*' --exclude secrets/ --exclude '*.p8' --exclude '*.pem' --exclude 'snowflake_rsa_key.pub' \
      --exclude node_modules/ --exclude .venv/ --exclude .next/ --exclude __pycache__/ \
      --exclude .pytest_cache/ --exclude .ruff_cache/ --exclude '*.tsbuildinfo' --exclude data/care_plans/ \
      ./ "$USER_AT:$REMOTE/"
    echo "→ Building and restarting on the server (first time ~5–8 min, then ~1–3 min)"
    remote "cd $REMOTE && test -f .env || { echo 'No .env on the server: run scripts/deploy.sh secrets'; exit 1; }
            $COMPOSE up -d --build --remove-orphans && docker image prune -f >/dev/null"
    echo "→ Waiting for https://$DOMAIN (the first start also gets the HTTPS certificate)"
    for _ in $(seq 1 60); do
      if curl -fsS --max-time 5 "https://$DOMAIN/api/health" >/dev/null 2>&1; then
        echo "✓ Live: https://$DOMAIN    (iPhone bed page: https://$DOMAIN/bed/120)"
        exit 0
      fi
      sleep 5
    done
    echo "✗ https://$DOMAIN did not answer within 5 min. Check: scripts/deploy.sh logs caddy   (DEPLOY.md → Troubleshooting)"
    exit 1
    ;;

  status)
    need_host
    remote "cd $REMOTE && $COMPOSE ps --format 'table {{.Service}}\t{{.Status}}'"
    if [ -n "$DOMAIN" ]; then
      curl -fsS --max-time 10 "https://$DOMAIN/api/health" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(f"sim {d[\"sim\"][\"mode\"]}, live loop {d[\"live_sim\"][\"phase\"]}, evaluator {d[\"evaluator\"][\"phase\"]} "
      f"({d[\"evaluator\"][\"avg_ms\"]:.0f} ms/cycle), sync {d[\"deep\"][\"sync\"]}, ws clients {d[\"ws_clients\"]}")' \
        || echo "https://$DOMAIN/api/health did not answer"
    fi
    ;;

  logs)
    need_host
    remote_tty "cd $REMOTE && $COMPOSE logs -f --tail=200 ${1:-}"
    ;;

  reset)
    need_host
    remote_tty "cd $REMOTE && $COMPOSE exec backend python scripts/reset_demo.py $*"
    ;;

  ssh)
    need_host
    remote_tty "cd $REMOTE && exec bash -l"
    ;;

  stop)
    need_host
    remote "cd $REMOTE && $COMPOSE down"
    echo "Stopped. The server still exists (and bills) until you destroy it in Vultr (DEPLOY.md → Destroy)."
    ;;

  *)
    sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac
