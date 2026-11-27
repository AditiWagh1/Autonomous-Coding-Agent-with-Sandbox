#!/usr/bin/env bash
# One-shot setup for an Ubuntu VM on Oracle Cloud, AWS EC2 or any other provider.
# Usage (as the normal 'ubuntu' user, from inside the cloned repo):
#     ./deploy/server-setup.sh your-name.duckdns.org
set -euo pipefail

DOMAIN="${1:?usage: ./deploy/server-setup.sh your-name.duckdns.org}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="$(whoami)"

echo "==> 1/7 System packages"
sudo apt-get update
sudo apt-get install -y docker.io python3-venv python3-pip git curl iptables gnupg \
     debian-keyring debian-archive-keyring apt-transport-https
sudo usermod -aG docker "$RUN_USER"

echo "==> 2/7 Caddy (automatic HTTPS)"
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update && sudo apt-get install -y caddy
fi

echo "==> 3/7 Swap file (safety net on small machines)"
if ! swapon --show | grep -q .; then
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> 4/7 Python environment"
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "==> 5/7 Sandbox image (first build takes a few minutes)"
sudo docker build -f docker/Dockerfile.sandbox -t sandbox-agent:latest docker

echo "==> 6/7 Settings file /etc/agent.env"
sudo mkdir -p /var/lib/agent-workspaces && sudo chown "$RUN_USER" /var/lib/agent-workspaces
if [ ! -f /etc/agent.env ]; then
  sudo tee /etc/agent.env >/dev/null <<ENV
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_FALLBACK_MODELS=gemini-3.8-flash
SANDBOX_BACKEND=docker
APP_PASSWORD=
SERVER_MODE=1
WORKSPACE_ROOT=/var/lib/agent-workspaces
MAX_CONCURRENT_RUNS=4
GITHUB_URL=
FEEDBACK_URL=
ENV
  sudo chmod 600 /etc/agent.env
fi

echo "==> 7/7 Services"
sudo tee /etc/systemd/system/sandbox-firewall.service >/dev/null <<UNIT
[Unit]
Description=Open web ports and restrict sandbox egress
After=docker.service netfilter-persistent.service
Requires=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash $APP_DIR/deploy/server-firewall.sh
[Install]
WantedBy=multi-user.target
UNIT

sudo tee /etc/systemd/system/agent.service >/dev/null <<UNIT
[Unit]
Description=Autonomous Coding Agent (Streamlit)
After=network-online.target docker.service sandbox-firewall.service
Requires=docker.service
[Service]
User=$RUN_USER
SupplementaryGroups=docker
WorkingDirectory=$APP_DIR
EnvironmentFile=/etc/agent.env
ExecStart=$APP_DIR/.venv/bin/streamlit run app.py --server.port 8501 --server.address 127.0.0.1
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT

sudo tee /etc/caddy/Caddyfile >/dev/null <<CADDY
$DOMAIN {
    reverse_proxy 127.0.0.1:8501
}
CADDY

sudo systemctl daemon-reload
sudo systemctl enable --now sandbox-firewall.service
sudo systemctl enable agent.service
sudo systemctl restart caddy

echo
echo "Setup finished. Now:"
echo "  1) sudo nano /etc/agent.env     # paste GEMINI_API_KEY, choose APP_PASSWORD"
echo "  2) sudo systemctl restart agent"
echo "  3) open https://$DOMAIN"
