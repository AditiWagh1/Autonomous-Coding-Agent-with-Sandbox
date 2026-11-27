#!/usr/bin/env bash
# Adds a second, PUBLIC (safe-mode) copy of the app next to the full-featured, password-protected one.
#   full version   -> your first DuckDNS name (access codes, every feature)
#   public version -> a second DuckDNS name (anyone can open it; offline sandbox, fair-use limits)
# Usage (after server-setup.sh worked):   ./deploy/add-public-instance.sh your-public-name.duckdns.org
set -euo pipefail

DOMAIN="${1:?usage: ./deploy/add-public-instance.sh your-public-name.duckdns.org}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="$(whoami)"

[ -f /etc/agent.env ] || { echo "Run ./deploy/server-setup.sh first."; exit 1; }

if [ ! -f /etc/agent-public.env ]; then
  sudo tee /etc/agent-public.env >/dev/null <<ENV
# Overrides /etc/agent.env for the public instance (this file is read second)
PUBLIC_DEMO=1
APP_PASSWORD=
SERVER_MODE=1
DAILY_RUN_CAP=100
MAX_RUNS_PER_SESSION=5
RUN_COOLDOWN_SEC=10
MAX_CONCURRENT_RUNS=2
PUBLIC_MODELS=gemini-3.5-flash-lite,gemini-3.8-flash
GEMINI_FALLBACK_MODELS=gemini-3.5-flash-lite
GITHUB_URL=
FEEDBACK_URL=
ENV
  sudo chmod 600 /etc/agent-public.env
fi

sudo tee /etc/systemd/system/agent-public.service >/dev/null <<UNIT
[Unit]
Description=Autonomous Coding Agent (public demo)
After=network-online.target docker.service sandbox-firewall.service
Requires=docker.service
[Service]
User=$RUN_USER
SupplementaryGroups=docker
WorkingDirectory=$APP_DIR
EnvironmentFile=/etc/agent.env
EnvironmentFile=/etc/agent-public.env
ExecStart=$APP_DIR/.venv/bin/streamlit run app.py --server.port 8502 --server.address 127.0.0.1
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT

if ! grep -q "$DOMAIN" /etc/caddy/Caddyfile; then
  sudo tee -a /etc/caddy/Caddyfile >/dev/null <<CADDY

$DOMAIN {
    reverse_proxy 127.0.0.1:8502
}
CADDY
fi

sudo systemctl daemon-reload
sudo systemctl enable --now agent-public.service
sudo systemctl reload caddy

echo
echo "Public instance started on https://$DOMAIN"
echo "Optional: sudo nano /etc/agent-public.env   (add GITHUB_URL / FEEDBACK_URL, change limits)  then: sudo systemctl restart agent-public"
