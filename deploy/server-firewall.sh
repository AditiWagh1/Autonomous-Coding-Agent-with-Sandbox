#!/usr/bin/env bash
# Run as root (the systemd unit does this at every boot, after Docker has started). Idempotent.
#  1) opens ports 80/443 in the default Ubuntu firewall of cloud images (Oracle blocks everything except SSH) (it only allows SSH out of the box)
#  2) stops sandbox containers that have internet from reaching cloud metadata, your private network and the host
set -euo pipefail

add() { iptables -C "$@" 2>/dev/null || iptables -I "$@"; }

# --- 1) web ports: insert BEFORE the final REJECT rule of the INPUT chain
pos=$(iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')
pos=${pos:-1}
for port in 80 443; do
  iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null \
    || iptables -I INPUT "$pos" -p tcp --dport "$port" -m state --state NEW -j ACCEPT
done

# --- 2) sandbox egress rules (see deploy/firewall-egress.sh)
BRIDGE_NET="$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Subnet}}' 2>/dev/null || echo 172.17.0.0/16)"
export BRIDGE_NET
bash "$(dirname "$0")/firewall-egress.sh"
echo "server-firewall: ports 80/443 open, sandbox egress rules active for $BRIDGE_NET"
