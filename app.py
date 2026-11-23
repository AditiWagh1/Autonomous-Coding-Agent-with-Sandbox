"""
app.py - Streamlit dashboard: self-repairing code agent + Antigravity-style terminal agent.
Run with:  streamlit run app.py
Env vars:  GEMINI_API_KEY, GEMINI_MODEL, SANDBOX_BACKEND (docker|e2b), E2B_API_KEY, APP_PASSWORD (optional)
"""

import hmac
import os

from datetime import datetime
from pathlib import Path

import streamlit as st

from agent import AutonomousCodingAgent, LLMClient, SandboxResult, TerminalAgent
from agent.artifacts import validate_artifacts
from agent.languages import LANGUAGES
from sandbox import backend_name, create_runner, create_terminal

st.set_page_config(page_title="Autonomous Coding Agent", layout="wide")

# ---- optional password gate (set APP_PASSWORD when the app is public) -------------------
_pw = os.getenv("APP_PASSWORD", "")
if _pw and not st.session_state.get("auth_ok"):
    st.title("🔒 Autonomous Coding Agent")
    entered = st.text_input("Password", type="password")
    if entered:
        if hmac.compare_digest(entered, _pw):
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()

BACKEND = backend_name()
NET_LOCKED = os.getenv("DISABLE_INTERNET_OPTION", "") == "1"   # set this on any public deployment
if BACKEND == "docker" and not st.session_state.get("reaped"):
    st.session_state["reaped"] = True
    try:  # remove terminal containers abandoned by closed browser tabs
        import docker
        from sandbox import reap_stale_terminals
        reap_stale_terminals(docker.from_env(), max_age_sec=3600)
    except Exception:
        pass
st.title("🤖 Autonomous Coding Agent with Sandbox")
st.caption(f"Execution backend: **{BACKEND}** · everything the model writes runs inside an isolated sandbox, never on this server.")

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("⚙️ Configuration")
    mode = st.radio("Mode", ["Code agent (self-repair)", "Terminal agent (Antigravity-style)"])
    model_name = st.text_input("Gemini model", value=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))

    st.subheader("🔐 Permissions")
    if NET_LOCKED:
        st.info("Internet access for generated code is disabled on this server.")
    allow_net = (not NET_LOCKED) and st.checkbox(
        "Allow internet for generated programs (scraping)",
        value=False,
        help="Off by default. Needed for tasks like web scraping. The sandbox still cannot see your files.",
    )
    if allow_net:
        st.warning("Generated code can reach the internet. Scrape politely and only sites that allow it.")
    save_outputs = st.checkbox("Also save output files to the ./outputs folder", value=True,
                               help="The sandbox's /output folder is temporary and lives inside Docker. This copies the files next to this project.")
    allowed_raw = st.text_input(
        "Pre-approved Python packages",
        placeholder="e.g. requests, beautifulsoup4, matplotlib",
        help="The code agent may only install packages listed here; anything else is denied.",
    )
    allowed = {p.strip().lower() for p in allowed_raw.split(",") if p.strip()}

    if mode.startswith("Code"):
        language = st.selectbox("Language", list(LANGUAGES), format_func=lambda k: LANGUAGES[k].label)
        max_retries = st.slider("Max attempts (self-repair)", 1, 5, 3)
        mem_limit = st.selectbox("Memory cap", ["256m", "512m", "1g"], index=0)
        timeout_sec = st.slider("Time limit per run (s)", 5, 60, 15)
    else:
        max_steps = st.slider("Max steps", 3, 30, 15)
        approval_mode = st.radio(
            "Ask before…", ["risky", "all"], format_func=lambda m: "internet/install commands only" if m == "risky" else "every command")
        workspace = ""
        if BACKEND == "docker":
            workspace = st.text_input(
                "Workspace folder (optional)", placeholder="blank = private temp folder",
                help="The agent can read, change and delete everything in this folder, and nothing outside it. Use a COPY of a project.")


def approve_install(packages):
    if NET_LOCKED:
        return False
    names = {p.split("[")[0].replace(">", "=").replace("<", "=").replace("~", "=").replace("!", "=").split("=")[0].strip().lower() for p in packages}
    return names.issubset(allowed)


def make_llm():
    return LLMClient(model_name=model_name)


OUTPUT_DIR = Path("outputs")


def save_files(files, label):
    """Copy files to ./outputs/<label>/ on THIS computer so you can open them in Explorer."""
    folder = OUTPUT_DIR / label
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (folder / os.path.basename(name)).write_bytes(data)
    return folder.resolve()


def show_artifacts(artifacts, key):
    for name, data in artifacts.items():
        problems = validate_artifacts({name: data})
        if problems:
            st.warning("⚠️ " + problems[0])
        elif name.lower().endswith((".png", ".jpg", ".jpeg", ".gif")):
            st.image(data, caption=name)
        st.download_button(f"⬇ Download {name}", data, file_name=os.path.basename(name), key=f"{key}-{name}")


# ================================================================== CODE AGENT
if mode.startswith("Code"):
    st.markdown("Describe a task. The agent will **Generate → Execute → Observe → Repair** and stop after a fixed number of attempts.")
    task = st.text_area("Task specification", height=100,
                        placeholder="e.g. Scrape the book titles and prices from http://books.toscrape.com and save a bar chart of the 10 cheapest to /output/prices.png")
    if st.button("🚀 Run agent", type="primary"):
        if not task.strip():
            st.warning("Enter a task first.")
            st.stop()
        try:
            llm = make_llm()
            sandbox = create_runner(mem_limit=mem_limit, allow_network=allow_net or (BACKEND == "e2b" and bool(allowed)))
        except Exception as e:
            st.error(f"Setup failed: {e}")
            st.stop()
        agent = AutonomousCodingAgent(llm, sandbox, max_retries=max_retries, verbose=False, approve_install=approve_install,
                                      timeout_sec=timeout_sec, language=language, allow_network=allow_net)
        try:
            with st.spinner("Agent is working..."):
                state = agent.run(task)
        finally:
            sandbox.close()

        if state.is_success:
            st.success(f"Solved in {len(state.iterations)} attempt(s) ({state.total_duration:.1f}s)")
        else:
            st.error(f"Stopped after {len(state.iterations)} failed attempt(s).")
            if state.final_explanation:
                st.markdown("### What went wrong")
                st.write(state.final_explanation)
        c1, c2, c3 = st.columns(3)
        c1.metric("Attempts", len(state.iterations))
        c2.metric("Total tokens", state.total_tokens)
        c3.metric("Duration", f"{state.total_duration:.1f}s")

        if state.is_success and save_outputs and state.iterations[-1].artifacts:
            folder = save_files(state.iterations[-1].artifacts, datetime.now().strftime("code-%Y%m%d-%H%M%S"))
            st.info(f"📁 Files saved on your computer in: {folder}")

        for req in state.install_requests:
            st.write(("✅ install approved: " if req["approved"] else "⛔ install denied: ") + ", ".join(req["packages"]))
        st.divider()
        for log in state.iterations:
            icon = "✅" if log.succeeded else "❌"
            with st.expander(f"{icon} Attempt {log.attempt} — exit {log.exit_code}{' (timed out)' if log.timed_out else ''} ({log.duration:.2f}s)",
                             expanded=(log.attempt == len(state.iterations))):
                st.code(log.code_generated or "(none)", language={"cpp": "cpp", "c": "c"}.get(state.language, "python"))
                if log.packages_requested:
                    st.markdown("**Requested packages:** " + ", ".join(log.packages_requested))
                if log.stdout.strip():
                    st.markdown("**STDOUT**"); st.code(log.stdout)
                if log.stderr.strip():
                    st.markdown("**STDERR**"); st.code(log.stderr)
                show_artifacts(log.artifacts, f"a{log.attempt}")

# ================================================================== TERMINAL AGENT
else:
    st.markdown(
        "The agent works in a real shell, one command at a time. Normal commands run automatically in an **offline** sandbox; "
        "anything that needs the internet (installs, downloads, scraping) **pauses and asks you**.")
    ss = st.session_state

    def end_session():
        sess = ss.pop("tsession", None)
        runner = ss.pop("trunner", None)
        ss.pop("tagent", None); ss.pop("tpending", None); ss.pop("tsaved", None)
        for obj in (sess, runner):
            try:
                obj and obj.close()
            except Exception:
                pass

    task = st.text_area("Task", height=100, disabled="tagent" in ss,
                        placeholder="e.g. Create a C program that prints the first 20 primes, compile it with gcc and run it.")
    c1, c2 = st.columns(2)
    if c1.button("▶ Start session", type="primary", disabled="tagent" in ss):
        if not task.strip():
            st.warning("Enter a task first.")
            st.stop()
        try:
            runner = create_runner(allow_network=allow_net)
            session = create_terminal(runner, workspace=workspace or None).start()
            if NET_LOCKED:  # refuse every networked command, whatever the user clicks
                _orig_run = session.run

                def _offline_run(command, timeout_sec=30, network=False, _o=_orig_run):
                    if network:
                        return SandboxResult("", "Internet access is disabled on this server.", 1, 0.0)
                    return _o(command, timeout_sec=timeout_sec, network=False)

                session.run = _offline_run
            agent = TerminalAgent(make_llm(), session, max_steps=max_steps, approval_mode=approval_mode)
            agent.start(task)
            ss.update(trunner=runner, tsession=session, tagent=agent, tpending=None)
        except Exception as e:
            st.error(f"Setup failed: {e}")
            st.stop()
    if c2.button("■ End session", disabled="tagent" not in ss):
        end_session()
        st.rerun()

    agent = ss.get("tagent")
    if agent:
        state = agent.state
        # advance until we need a human, or we are finished
        if not state.finished and ss.get("tpending") is None:
            with st.spinner("Agent is thinking..."):
                try:
                    ss["tpending"] = agent.propose()
                except Exception as e:
                    st.error(f"Model error: {e}")
                    st.stop()
            pend = ss["tpending"]
            if pend is not None and pend.decision.kind != "ask":
                with st.spinner(f"$ {pend.action.command}"):
                    agent.execute(pend, approved=pend.decision.kind == "auto")
                ss["tpending"] = None
                st.rerun()
            elif pend is None:
                st.rerun()

        # timeline
        for s in state.steps:
            icon = {"ran": "✅" if s.exit_code == 0 else "⚠️", "denied": "🚫", "blocked": "⛔"}[s.outcome]
            with st.expander(f"{icon} Step {s.step}: `{s.command[:80]}`" + (" 🌐" if s.network else ""), expanded=(s is state.steps[-1])):
                if s.thought:
                    st.caption(s.thought)
                st.code(f"$ {s.command}", language="bash")
                if s.outcome != "ran":
                    st.warning(f"{s.outcome.upper()}: {s.reason or 'denied by you'}")
                if s.stdout.strip():
                    st.code(s.stdout)
                if s.stderr.strip():
                    st.code(s.stderr)

        pend = ss.get("tpending")
        if pend is not None and not state.finished:
            st.warning(f"**Approval needed — step {pend.step}** ({pend.decision.reason})")
            if pend.action.thought:
                st.caption(pend.action.thought)
            st.code(f"$ {pend.action.command}", language="bash")
            a, b = st.columns(2)
            if a.button("✅ Approve & run"):
                agent.execute(pend, approved=True); ss["tpending"] = None; st.rerun()
            if b.button("🚫 Deny"):
                agent.execute(pend, approved=False); ss["tpending"] = None; st.rerun()

        if state.finished:
            if state.success:
                st.success(f"Done in {len(state.steps)} step(s): {state.summary}")
            else:
                st.error(f"Stopped: {state.stop_reason}")
                if state.explanation:
                    st.write(state.explanation)
                if state.invalid_samples:
                    with st.expander("Raw replies the app could not understand"):
                        for sample in state.invalid_samples:
                            st.code(sample)
            st.caption(f"Tokens used: {state.total_tokens}")
            files = ss["tsession"].list_files()
            if files and save_outputs and not ss.get("tsaved"):
                found = {f: ss["tsession"].read_file(f) for f in files}
                folder = save_files({k: v for k, v in found.items() if v is not None},
                                    datetime.now().strftime("terminal-%Y%m%d-%H%M%S"))
                ss["tsaved"] = str(folder)
            if ss.get("tsaved"):
                st.info(f"📁 Files saved on your computer in: {ss['tsaved']}")
            if files:
                st.subheader("📁 Workspace files")
                for f in files:
                    data = ss["tsession"].read_file(f)
                    if data is not None:
                        st.download_button(f"Download {f}", data, file_name=os.path.basename(f), key=f"dl-{f}")
                        if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif")):
                            st.image(data, caption=f)
