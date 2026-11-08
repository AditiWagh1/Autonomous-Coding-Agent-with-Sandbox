# Deploying the Autonomous Coding Agent

## 1. Why Render's free plan cannot run the Docker sandbox

Your app launches a **new container for every run** (`docker.from_env()`). That needs access to a Docker
daemon. A PaaS container only has that in two ways, and both are unavailable or unsafe:

| Technique | What it needs | Verdict |
|---|---|---|
| **Docker-in-Docker (DinD)** | the outer container must run `--privileged` | Render does not allow privileged containers (Render staff say so on their community forum). |
| **Docker-out-of-Docker** (mount `/var/run/docker.sock`) | access to the host's daemon | Not offered on Render. Even where it is possible, whoever controls the socket is **root on the host**, so model-written code that escapes would own the machine. Never mount the socket into a public-facing app. |

So the security rule is not an obstacle to work around, it is the reason the deployment must use
**VM-level isolation** (a real VM, or a micro-VM service) instead of containers-inside-your-web-app.

## 2. Pick one of three deployments (all free)

| | A. Cloud VM with Docker (recommended for full features) | B. Render (UI) + E2B micro-VMs (easiest URL) | C. Your laptop + tunnel (safest demo day) |
|---|---|---|---|
| Where the app runs | A free VM you control, e.g. Oracle Cloud *Always Free* | Render free web service | Your PC |
| Where model code runs | Docker containers on that VM (sibling containers, no socket inside any container) | E2B Firecracker micro-VM | Docker Desktop |
| Terminal agent with real offline/online split | Yes (physical split) | Yes, but internet is per-session, not per-command | Yes |
| Cost | Free tier; needs a card for ID checks | Free: E2B Hobby has a one-time $100 credit, no card | Free |
| Risk | You maintain a server | Free tier limits (1 h sessions, credits run out) | PC must stay on |

Oracle's free ARM VM changed over time (sources report 4 OCPU/24 GB, and at least one reports a cut to
2 OCPU/12 GB) and "out of capacity" errors are common. Check Oracle's *Always Free Resources* page before relying on it.
Any 1-2 GB VPS works the same way.

---

## 3A. Cloud VM with Docker

The app runs **directly on the VM** (not inside a container) so no container ever needs the Docker socket.

```bash
# on the VM (Ubuntu), as a normal user
sudo apt update && sudo apt install -y docker.io python3-venv git
sudo usermod -aG docker $USER && newgrp docker
git clone <your-repo> sandbox && cd sandbox
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
docker build -f docker/Dockerfile.sandbox -t sandbox-agent:latest docker      # one-time (or let the app build it)

sudo BRIDGE_NET=172.17.0.0/16 ./deploy/firewall-egress.sh                      # block metadata/LAN/host from sandboxes

export GEMINI_API_KEY=...  GEMINI_MODEL=gemini-3.8-flash  APP_PASSWORD='choose-a-long-one'
streamlit run app.py --server.port 8501 --server.address 127.0.0.1
```
Expose it with a tunnel (no open ports): `cloudflared tunnel --url http://localhost:8501`, or put Caddy/nginx with HTTPS in front.
Run it under `systemd` or `tmux` so it survives logouts.

Checklist before sharing the URL:
- `APP_PASSWORD` is set (anyone with the link can otherwise run code and burn **your** Gemini quota).
- `deploy/firewall-egress.sh` applied and verified; the VM has no secrets on disk other than the API keys.
- Only ports 22 (key login) and your reverse proxy are open.
- Run `python -m evals.security_tests` on the VM and keep the output for your report.

## 3B. Render (UI) + E2B (execution)

1. Create an E2B account (free Hobby plan) and copy the API key.
2. Push the repo to GitHub. On Render: **New > Web Service**, runtime *Python*.
   - Build command: `pip install -r requirements.txt -r requirements-e2b.txt`
   - Start command: `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`
3. Environment variables: `SANDBOX_BACKEND=e2b`, `E2B_API_KEY`, `GEMINI_API_KEY`, `GEMINI_MODEL=gemini-3.8-flash`, `APP_PASSWORD`.
4. Free Render services sleep when idle, so the first request after a pause is slow: open the URL a few minutes before your demo.

Notes for this backend (put these in your report as limitations):
- The E2B code was written against the v2 Python SDK and could not be tested against a live account here; the few SDK calls are marked `[SDK]` in `sandbox/e2b_backend.py`. Check that `gcc`/`g++` exist in your sandbox template (`which gcc` via the terminal agent) or the C/C++ options will fail.
- Internet is chosen **when the sandbox starts** (the "Allow internet" box, or any pre-approved package). Pip installs therefore need internet on for the whole session.
- Memory/CPU/time limits come from E2B, not from this repository.

## 3C. Laptop + tunnel

```bash
streamlit run app.py
cloudflared tunnel --url http://localhost:8501     # or: ngrok http 8501
```
Same security as local use. Set `APP_PASSWORD` first because the URL is public while the tunnel runs.

## 3D. Public demo checklist (any option)

- Set `APP_PASSWORD` and share it only with people you trust: the link is public, so without it anyone can run code and use your Gemini quota.
- Set `DISABLE_INTERNET_OPTION=1` on a public server. This hides the internet checkbox and refuses every networked command, so strangers cannot use your connection for scraping or attacks. Keep it off only for your own supervised demo.
- Quick tunnels (`cloudflared tunnel --url ...`) give a random, temporary address that stops working when you close the command. Only a named tunnel or a hosted service gives a permanent link.
- On Windows/Docker Desktop the Linux firewall script does not apply, which is one more reason to keep internet off for public use.

## 4. Security summary you can cite

- Model-written code never runs in the web app process; it runs in a container (or micro-VM) that is non-root, has no capabilities, a read-only root filesystem, CPU/RAM/PID/time limits and **no network** unless you opt in.
- The terminal agent's long-lived container is always offline; commands that need the internet run in a separate short-lived container that shares only the workspace folder.
- Package installs are human-approved, validated against a strict name pattern and wheel-only (no `setup.py` execution).
- The command policy (block/ask/auto) is defence in depth, not the boundary; the containers are the boundary.
- Residual risks: container escapes via kernel bugs (the reason for VM-level isolation in public deployments), abuse of an opted-in internet connection (scraping sites you should not, hitting third parties), and quota exhaustion (mitigated by `APP_PASSWORD`).
