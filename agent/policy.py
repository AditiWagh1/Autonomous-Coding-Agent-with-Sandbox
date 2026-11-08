"""
agent/policy.py - Command gate for the terminal agent.

Three outcomes:
  block - never run (privilege escalation, docker, destructive system commands)
  ask   - needs a human "yes" first (anything that needs the internet, or every command in 'all' mode)
  auto  - runs immediately in the offline sandbox

IMPORTANT: this is a usability and defence-in-depth layer, NOT the security boundary. Patterns can be
evaded (e.g. `python -c "import os; os.system(...)"`). The real protection is the container itself:
no network, read-only root FS, non-root, no capabilities, resource limits, and only the workspace mounted.
"""

import re
from dataclasses import dataclass
from typing import List, Tuple

DENY: List[Tuple[str, str]] = [
    (r"\bsudo\b|(^|[;&|]\s*)su(\s|$)", "privilege escalation"),
    (r"\bdocker\b|docker\.sock|\bpodman\b|\bkubectl\b", "container tooling is not allowed"),
    (r"\b(mount|umount|chroot|nsenter|unshare|modprobe|insmod|iptables|sysctl)\b", "system administration command"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "power command"),
    (r"\bmkfs|\bdd\s+[^|;]*of=/dev/", "disk-destroying command"),
    (r":\(\)\s*\{", "fork bomb"),
    (r"\brm\s+(-[A-Za-z]+\s+)*(/|/\*|~|\$HOME|\.\.)(\s|$|/\*)", "deleting outside the workspace"),
    (r"/proc/(sys|[0-9]+/(mem|environ))|/sys/", "kernel interface access"),
]

NETWORK: List[str] = [
    r"\bpip3?\s+(install|download)\b",
    r"python3?\s+-m\s+pip\b",
    r"\b(npm|yarn|pnpm)\s+(install|i|add|ci)\b",
    r"\bapt(-get)?\b",
    r"\b(curl|wget|ssh|scp|rsync|nc|ncat|telnet|ftp)\b",
    r"\bgit\s+(clone|fetch|pull|push|submodule)\b",
    r"https?://",
]


@dataclass
class Decision:
    kind: str            # "block" | "ask" | "auto"
    reason: str = ""
    network: bool = False


def classify(command: str, network_flag: bool = False, mode: str = "risky") -> Decision:
    cmd = (command or "").strip()
    if not cmd:
        return Decision("block", "empty command")
    if len(cmd) > 4000 or "\x00" in cmd:
        return Decision("block", "command too long or contains NUL bytes")
    for pattern, why in DENY:
        if re.search(pattern, cmd, re.IGNORECASE):
            return Decision("block", why)
    needs_net = network_flag or any(re.search(p, cmd, re.IGNORECASE) for p in NETWORK)
    if needs_net:
        return Decision("ask", "needs internet access (runs in a separate network-enabled container)", True)
    if mode == "all":
        return Decision("ask", "approval mode: confirm every command", False)
    return Decision("auto")
