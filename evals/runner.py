"""
evals/runner.py - Benchmark harness.  Run from the project root:

    python -m evals.runner                 # all tasks
    python -m evals.runner --skip-network  # skip tasks that need pip install
    python -m evals.runner --retries 3 --delay 5

Headline metric (matches the project brief):
    "resolved X% of runtime errors within N attempts"
An error counts as 'encountered' when attempt 1 fails (seeded bugs guarantee this for
the s* tasks; the LLM's own mistakes count too). It counts as 'resolved' when a later
attempt passes AND the output contains the expected text.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import AutonomousCodingAgent, LLMClient  # noqa: E402
from sandbox import DockerSandboxRunner  # noqa: E402


def run_evaluation_suite(dataset_path: str, max_retries: int = 3, delay: float = 5.0,
                         skip_network: bool = False, model: str = "gemini-3.8-flash"):
    with open(dataset_path, "r", encoding="utf-8") as f:
        tasks = json.load(f)
    if skip_network:
        tasks = [t for t in tasks if t.get("category") not in ("needs_package", "needs_network")]

    llm = LLMClient(model_name=model)
    rows = []
    print(f"\n=== BENCHMARK: {len(tasks)} tasks, max {max_retries} attempts each ===\n")

    with DockerSandboxRunner(mem_limit="256m", network_disabled=True) as sandbox:
        for i, item in enumerate(tasks, 1):
            approved = {p.lower() for p in item.get("approved_packages", [])}
            agent = AutonomousCodingAgent(
                llm, sandbox, max_retries=max_retries, verbose=False,
                approve_install=lambda pkgs, a=approved: {p.split("[")[0].split("=")[0].split(">")[0].split("<")[0].lower() for p in pkgs} <= a,
                explain_failures=False,
                language=item.get("language", "python"),
                allow_network=item.get("allow_network", False),
            )
            print(f"[{i}/{len(tasks)}] {item['name']} ({item['id']}) ...", end=" ", flush=True)
            state = agent.run(item["prompt"], initial_code=item.get("initial_code"))

            expected = item.get("expected_output_contains", "").strip()
            output = (state.final_output or "").strip()
            passed = state.is_success and (expected in output if expected else True)
            errored = state.first_attempt_failed
            recovered = errored and passed
            status = "FAIL" if not passed else ("PASS (recovered)" if recovered else "PASS (zero-shot)")
            print(f"{status} [{len(state.iterations)} attempts, {state.total_tokens} tokens]")

            rows.append({
                "id": item["id"], "category": item.get("category"), "language": item.get("language", "python"), "name": item["name"],
                "passed": passed, "errored_first_attempt": errored, "recovered": recovered,
                "attempts": len(state.iterations), "tokens": state.total_tokens,
                "duration_sec": round(state.total_duration, 2), "output": output[:100],
            })
            time.sleep(delay)  # stay under the Gemini free-tier rate limit

    total = len(rows)
    passed = sum(r["passed"] for r in rows)
    zero_shot = sum(r["passed"] and not r["errored_first_attempt"] for r in rows)
    errored = sum(r["errored_first_attempt"] for r in rows)
    recovered = sum(r["recovered"] for r in rows)
    rec_rate = (recovered / errored * 100) if errored else 0.0
    avg_attempts = sum(r["attempts"] for r in rows) / total if total else 0
    tokens = sum(r["tokens"] for r in rows)

    print("\n================ BENCHMARK REPORT ================")
    print(f"Tasks                : {total}")
    print(f"Final pass rate      : {passed / total * 100:.1f}% ({passed}/{total})")
    print(f"Zero-shot pass rate  : {zero_shot / total * 100:.1f}% ({zero_shot}/{total})")
    print(f"Runtime errors seen  : {errored}")
    print(f"Errors resolved      : {recovered}/{errored}")
    print(f"==> Resolved {rec_rate:.1f}% of runtime errors within {max_retries} attempts")
    print(f"Avg attempts/task    : {avg_attempts:.2f}")
    print(f"Total tokens         : {tokens}")
    print("==================================================")

    out = Path("evals/benchmark_results.json")
    out.write_text(json.dumps({
        "metrics": {
            "total_tasks": total, "pass_rate": passed / total * 100,
            "zero_shot_rate": zero_shot / total * 100, "errors_encountered": errored,
            "errors_resolved": recovered, "error_resolution_rate": rec_rate,
            "max_attempts": max_retries, "avg_attempts": avg_attempts, "total_tokens": tokens,
        },
        "task_runs": rows,
    }, indent=2), encoding="utf-8")
    print(f"\nSaved {out.resolve()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="evals/tasks.json")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--delay", type=float, default=5.0)
    ap.add_argument("--skip-network", action="store_true")
    ap.add_argument("--model", default=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    a = ap.parse_args()
    run_evaluation_suite(a.dataset, a.retries, a.delay, a.skip_network, a.model)
