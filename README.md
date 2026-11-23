# Sandbox - Autonomous Coding Agent

Two agents on top of one hardened sandbox:

* **Code agent**: plain-English task -> writes Python / C / C++ -> runs it in an isolated container ->
  reads the compiler/runtime error -> repairs and retries (max N attempts) -> if it still fails it stops and
  explains what went wrong. Optional internet (scraping) and human-approved package installs.
* **Terminal agent** (Antigravity-style): works in a real shell one command at a time. Everything runs in an
  offline container; commands that need the internet pause and ask you. Blocked commands never run.

## Setup
    pip install -r requirements.txt
    set GEMINI_API_KEY=your_key            (Windows)   /   export GEMINI_API_KEY=...   (mac/linux)
    set GEMINI_MODEL=gemini-3.8-flash      (optional, this is the default)
    # start Docker Desktop (the sandbox image is built automatically on first run)

## Run
    python test_local.py && python test_terminal.py     # offline logic tests (no key, no Docker)
    python -m evals.security_tests                      # proves the sandbox contains hostile code (Docker)
    streamlit run app.py                                # dashboard (both modes)
    python main.py --lang cpp "task"                    # CLI code agent  (python | c | cpp)
    python main.py --net "scrape ..."                   # allow internet for the generated program
    python main.py --terminal "task"                    # CLI terminal agent
    python -m evals.runner [--skip-network]             # benchmark: "resolved X% of runtime errors within 3 attempts"

## Deploy
See DEPLOY.md: public demo on Render + E2B (no API key needed from visitors), fair-use limits, checklist.
Key environment variables: GEMINI_API_KEY, GEMINI_MODEL, SANDBOX_BACKEND (docker|e2b), E2B_API_KEY, PUBLIC_DEMO, DAILY_RUN_CAP, APP_PASSWORD.

## Safety model
RUN/terminal containers: non-root, all capabilities dropped, read-only root FS, 256-512 MB RAM, 1 CPU, 64-128 PIDs,
hard timeouts, truncated output, no network unless opted in. Only throw-away folders (or the one workspace you choose)
are mounted; your files and docker.sock never are. Installs: human-approved, name-validated, wheels only.
