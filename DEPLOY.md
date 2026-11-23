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
