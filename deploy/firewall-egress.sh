#!/usr/bin/env bash
# Run ONCE as root on the Docker host that serves your public demo (Oracle/any VPS).
# Purpose: when a sandbox container is allowed internet (scraping / pip install), stop it from
#   1) reaching cloud metadata (169.254.169.254) -> would leak cloud credentials
#   2) reaching your private network / other VMs (SSRF into 10.x, 172.16.x, 192.168.x)
#   3) reaching services on the host itself (SSH, the Streamlit app, databases)
# Review before running. It only ADDS rules (idempotent) and flushes nothing.
# To keep the rules after reboot:  sudo apt install iptables-persistent && sudo netfilter-persistent save
set -euo pipefail

BRIDGE_NET="${BRIDGE_NET:-172.17.0.0/16}"   # default docker0 subnet: check with `docker network inspect bridge`

add() { iptables -C "$@" 2>/dev/null || iptables -I "$@"; }

for dst in 169.254.0.0/16 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10; do
  add DOCKER-USER -s "$BRIDGE_NET" -d "$dst" -j DROP
done
# containers may answer connections but must not open new ones to the host
add INPUT -s "$BRIDGE_NET" -m conntrack ! --ctstate ESTABLISHED,RELATED -j DROP

echo "Done. Verify from a networked sandbox container:  curl -m 3 http://169.254.169.254   (must time out)"
