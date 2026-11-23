"""
main.py - Command-line entry point.

  python main.py "task"                         # code agent, Python
  python main.py --lang cpp "task"              # code agent, C++ (c | cpp | python)
  python main.py --net "scrape ..."             # allow internet for the generated program
  python main.py --terminal "task"              # Antigravity-style terminal agent
"""

import argparse
import os
from datetime import datetime
from pathlib import Path

from agent import AutonomousCodingAgent, LLMClient, TerminalAgent
from sandbox import create_runner, create_terminal


def yes(prompt: str) -> bool:
    return input(prompt).strip().lower() in ("y", "yes")


def ask_install(packages):
    print("\n[APPROVAL NEEDED] The agent wants to install (internet, sandbox only):")
    for p in packages:
        print(f"   - {p}")
    return yes("Approve? [y/N] ")


def ask_command(pending):
    print(f"\n[APPROVAL NEEDED] step {pending.step}: {pending.decision.reason}")
    print(f"   $ {pending.action.command}")
    return yes("Run it? [y/N] ")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("task", nargs="*")
    ap.add_argument("--lang", default="python", choices=["python", "c", "cpp"])
    ap.add_argument("--net", action="store_true", help="allow internet while the generated program runs")
    ap.add_argument("--terminal", action="store_true", help="terminal-agent mode")
    ap.add_argument("--workspace", default=None, help="terminal mode: host folder the agent may use (default: private temp folder)")
    ap.add_argument("--confirm-all", action="store_true", help="terminal mode: ask before every command")
    ap.add_argument("--model", default=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    a = ap.parse_args()

    task = " ".join(a.task) or "Write a program that prints the factorial of 10."
    llm = LLMClient(model_name=a.model)
    print(f"Task: {task}\n")

    with create_runner(allow_network=a.net) as runner:
        if a.terminal:
            with create_terminal(runner, workspace=a.workspace) as session:
                agent = TerminalAgent(llm, session, approval_mode="all" if a.confirm_all else "risky")
                agent.start(task)
                while True:
                    pending = agent.propose()
                    if pending is None:
                        break
                    if pending.decision.kind == "ask":
                        ok = ask_command(pending)
                    else:
                        ok = pending.decision.kind == "auto"
                        print(f"\n[{pending.decision.kind}] $ {pending.action.command}")
                    log = agent.execute(pending, ok)
                    print((log.stdout or log.stderr).strip()[-800:])
                    if agent.state.finished:
                        break
                st = agent.state
                print(f"\n--- {'SUCCESS' if st.success else 'STOPPED: ' + st.stop_reason} | {len(st.steps)} steps | {st.total_tokens} tokens ---")
                print(st.summary or st.explanation or "")
                print("Workspace files:", session.list_files())
        else:
            agent = AutonomousCodingAgent(llm, runner, max_retries=3, approve_install=ask_install,
                                          language=a.lang, allow_network=a.net)
            result = agent.run(task)
            s = result.summary()
            print(f"\nSuccess: {s['success']} | Attempts: {s['attempts']} | Tokens: {s['total_tokens']} | {s['total_duration_sec']}s")
            if result.is_success:
                print("Output:\n" + (s["final_output"] or ""))
                files = result.iterations[-1].artifacts
                if files:
                    out = Path("outputs") / datetime.now().strftime("code-%Y%m%d-%H%M%S")
                    out.mkdir(parents=True, exist_ok=True)
                    for name, data in files.items():
                        (out / os.path.basename(name)).write_bytes(data)
                    print(f"Saved files to {out.resolve()}")
            else:
                print("Last error:\n" + (s["final_error"] or ""))
                print("\nWhat went wrong:\n" + (s["final_explanation"] or ""))
