# Deploying the Autonomous Coding Agent (public demo)

## The professional setup in one picture

```
Visitor (LinkedIn link)
        │  HTTPS
        ▼
Render web service  ── runs app.py only. Holds your keys as secrets, enforces limits.
        │  API calls                      │  API calls
        ▼                                 ▼
Google Gemini  (server-side key)      E2B micro-VM  (runs the model-written code, offline)
```

* **Visitors never enter an API key.** The app uses your key from the server's environment; it is never sent to the browser.
* **Model-written code never runs on the web server.** It runs in a separate E2B micro-VM. No Docker socket, no privileged container, nothing to escape into.
* **Public mode is deliberately conservative**: offline sandbox, no package installs, short time limits, fair-use caps. The full feature set (internet scraping, workspace, C/C++) is shown in your demo video and on GitHub.

Why not Docker on Render? Render cannot run privileged containers or expose a Docker daemon, and mounting a Docker socket into a public app would give an attacker root on the host. Micro-VM isolation is the correct pattern for strangers' code.

## Full-feature deployment for your examiner (same code, password-protected)

`render.yaml` defines two services from one codebase:

| | Public demo | Full version |
|---|---|---|
| Setting | `PUBLIC_DEMO=1` | no `PUBLIC_DEMO`, `APP_PASSWORD` set |
| Audience | LinkedIn visitors | examiner and team (they get the password) |
| Internet toggle, pre-approved packages, installs, terminal agent with approvals, model box | locked | **live** |

What still differs from the local Docker version (say this openly): the cloud sandbox has no per-container memory slider and no workspace-folder mount, and internet is chosen per session instead of per command. For 100% parity run the app on a VM with Docker (see "Other deployment options").

Why the public one is restricted: unrestricted internet and installs would give strangers free outbound internet and compute from your accounts. Restricting a public endpoint to least privilege is a deliberate security decision, not a missing feature.

## Step 1: test the cloud backend on your laptop first (30 min)

The E2B backend is written against the E2B v2 Python SDK and has not been run against a real account, so verify it before anyone else can see it.

```bash
pip install -r requirements.txt
set E2B_API_KEY=...  &  set GEMINI_API_KEY=...              (Windows; use export on mac/linux)
set SANDBOX_BACKEND=e2b & set PUBLIC_DEMO=1 & set ENABLED_LANGUAGES=python
streamlit run app.py
```
Run the four built-in examples in both modes. If something fails, the E2B calls are marked `[SDK]` in `sandbox/e2b_backend.py`; send me the error text. To enable C/C++ later, run the terminal example "C primes" and confirm `gcc` exists in the E2B sandbox, then add `c,cpp` to `ENABLED_LANGUAGES`.

## Step 2: GitHub

Push the project to a GitHub repository (public is fine). Before pushing check that no key is in any file (`.env` is already in `.gitignore`), and add screenshots plus a short architecture section to the README.

## Step 3: Render

1. Create an E2B account (free Hobby plan) and a Render account.
2. Render: **New > Blueprint**, select the repo. `render.yaml` creates the service. Paste `GEMINI_API_KEY` and `E2B_API_KEY` when asked, and fill `GITHUB_URL` and `FEEDBACK_URL`.
3. Wait for the build, open the URL, run all examples once. That is your live link.

## Gemini quota, cost and privacy (read before posting)

* A key shared by everyone has one quota. The free tier is easy to exhaust, so the app caps runs (below) and shows a friendly "at capacity" message instead of an error.
* Free-tier content may be used by Google to improve its products (check the current Gemini API terms). The app tells visitors not to enter personal data. If you want to avoid that, use a billing-enabled key with a low budget alert.
* Create a **separate key just for the demo** so you can revoke it without touching your other projects.

## Fair-use controls (environment variables)

| Variable | Public default | Meaning |
|---|---|---|
| `PUBLIC_DEMO` | `1` on Render | Shows every control; safe ones work (model, attempts, time limit, language, examples), the rest are visible but locked with an explanation. Internet and installs stay off. |
| `PUBLIC_MODELS` | server default | Comma-separated models visitors can choose; the first is the default. |
| `GEMINI_FALLBACK_MODELS` | none | Backup models tried automatically when the chosen model is overloaded (503) or rate-limited (429). |
| `MAX_RUNS_PER_SESSION` | 5 | Runs per visit (a page reload starts a new visit). |
| `DAILY_RUN_CAP` | 150 | All visitors together; this is what really protects your quota and credits. |
| `RUN_COOLDOWN_SEC` | 10 | Minimum gap between runs per visit. |
| `MAX_CONCURRENT_RUNS` | 3 | Simultaneous code runs. |
| `ENABLED_MODES` | `code,terminal` | Use `code` to show only the code agent. |
| `ENABLED_LANGUAGES` | all | e.g. `python` until C/C++ is verified on E2B. |
| `APP_PASSWORD` | unset | Optional: private preview for your team before going public. |

These limits reset when the server restarts, and they are not per-person (no accounts), so treat them as budget protection rather than abuse-proof security.

## Cold starts on Render's free plan

Free web services spin down after 15 minutes without traffic and take up to about a minute to wake up (Render's docs), and Render can restart them at any time. Visitors from a LinkedIn post will often hit a sleeping app. Options: open the link yourself a few minutes before posting; ping `/_stcore/health` every ~10 minutes with a free cron service (uses your 750 free monthly instance hours, enough for one always-on service); or pay for an always-on instance. Mention in the post that the first load may take a minute.

## Pre-launch checklist

- [ ] E2B backend tested locally (Step 1), all examples pass.
- [ ] `PUBLIC_DEMO=1`, caps set, demo-only Gemini key, E2B budget checked.
- [ ] Private preview with `APP_PASSWORD` for your team first; remove it to go public.
- [ ] Opened the live link on your phone.
- [ ] README has screenshots, architecture, safety model and limitations.
- [ ] A 60-90 second screen recording of the full local version (scraping, terminal approvals, blocked `sudo`) for LinkedIn.

## Oracle Cloud Always Free VM with Docker (full features, identical to local)

Oracle's documentation (updated June 2026) lists for Always Free: one Arm VM (`VM.Standard.A1.Flex`) with 2 OCPUs and 12 GB in total, or two tiny AMD `E2.1.Micro` VMs with 1 GB each. Always Free instances must live in your home region, "out of host capacity" errors are possible (retry, try another availability domain, or upgrade to Pay As You Go, which still only charges above the free limits), and Oracle may reclaim instances that stay idle for 7 days (CPU, network and, on Arm, memory all below 20%).

Scripts in `deploy/`: `server-setup.sh` (installs everything), `server-firewall.sh` (opens 80/443 and restricts sandbox egress, re-applied at every boot), `firewall-egress.sh`. Run:

```bash
git clone https://github.com/YOUR-USER/YOUR-REPO sandbox && cd sandbox
./deploy/server-setup.sh your-name.duckdns.org
sudo nano /etc/agent.env && sudo systemctl restart agent
```

## Two links from one server: full (private) and public (safe)

After `server-setup.sh` works, create a second DuckDNS name (e.g. `yourname-demo.duckdns.org`, same IP) and run `./deploy/add-public-instance.sh yourname-demo.duckdns.org`. You then have:

| Link | Access | Features |
|---|---|---|
| `yourname-agent.duckdns.org` | access codes only | everything |
| `yourname-demo.duckdns.org` | anyone (LinkedIn) | offline sandbox, model choice, languages, terminal agent, fair-use limits; internet and installs locked |

If `APP_PASSWORD` is empty on the full instance, anyone can use every feature including internet and installs, so never leave it empty.

## AWS EC2 instead of Oracle (same scripts)

The scripts are plain Ubuntu, so they work on AWS too: launch an Ubuntu 24.04 EC2 instance (at least 2 vCPU / 2 GB: for example `t3.small`; `t3.medium` is more comfortable), a 30 GB gp3 disk, and a security group allowing 22 (your IP only), 80 and 443. Point your DuckDNS name at the instance (use an Elastic IP so it survives restarts; AWS charges a small hourly fee for public IPv4 addresses), SSH in, clone the repo and run `./deploy/server-setup.sh your-name.duckdns.org`. New AWS accounts (created on or after 15 July 2025) get credits instead of the old 12-month free tier; check Billing for what applies to yours, create a budget alert, and stop (not terminate) the instance after your exam.

## Same features as local, with a few server-side differences

With the Docker backend the deployed app runs the same code path as your laptop: language choice, internet toggle, pre-approved packages, memory cap, terminal agent with approvals, scraping, charts. On a shared server three things behave differently by design:

| Laptop | Server (`SERVER_MODE=1`) |
|---|---|
| "Workspace folder" takes any path on your PC | "Workspace name": a folder inside `/var/lib/agent-workspaces` (a path box on a server would expose the server's disk) |
| Output files saved to `./outputs` | Files are delivered with download buttons (they would otherwise sit on the server) |
| You are the only user | Resources are shared: `MAX_CONCURRENT_RUNS` limits simultaneous runs, `APP_PASSWORD` can hold several comma-separated access codes (one per person; remove a code to revoke access) |

## Other deployment options

* **VM with Docker** (e.g. Oracle Always Free, any VPS): full features including real offline/online container split. Run the app directly on the VM (not in a container), run `deploy/firewall-egress.sh`, set `APP_PASSWORD`, `PUBLIC_DEMO=1`, and expose it through a tunnel or HTTPS proxy. More setup and a larger attack surface than E2B for strangers' code.
* **Laptop + tunnel** (`cloudflared tunnel --url http://localhost:8501`): good for a live presentation; the link is temporary and works only while your PC runs.

## Security summary for your report

* Generated code runs in a micro-VM (cloud) or a hardened container (local): non-root, no network in public mode, resource and time limits, output truncated.
* Package installs are human-approved or disabled; wheels only; strict name validation.
* The command policy (block/ask/auto) is defence in depth; the sandbox is the boundary.
* Secrets live only in server environment variables; visitors cannot supply or see keys.
* Fair-use caps, cooldown and concurrency limits protect quota and cost.
* Residual risks: sandbox-provider vulnerabilities, per-session limits bypassed by reloading (the daily cap still holds), and shared-quota exhaustion.
