"""
agent/orchestrator.py - Generate -> Execute -> Observe -> Repair loop with a hard retry cap.
"""

import re
import time
from typing import Callable, List, Optional, Protocol, runtime_checkable

from .parsing import parse_response
from .prompts import (
    build_system_prompt,
    SYSTEM_EXPLAINER_PROMPT,
    build_user_task_prompt,
    build_feedback_prompt,
    build_explanation_prompt,
)
from .languages import get_language
from .state import AgentState, SandboxResult
from .llm import LLMClient

ApprovalCallback = Callable[[List[str]], bool]


@runtime_checkable
class SandboxRunner(Protocol):
    """What the agent needs from a sandbox. install_packages is optional."""
    def run_code(self, code: str, timeout_sec: int = 15, language: str = "python", allow_network: bool = False) -> SandboxResult:
        ...


def _pkg_key(requirement: str) -> str:
    return re.split(r"[<>=!~\[]", requirement)[0].strip().lower()


class AutonomousCodingAgent:
    def __init__(
        self,
        llm_client: LLMClient,
        sandbox: SandboxRunner,
        max_retries: int = 3,
        verbose: bool = True,
        approve_install: Optional[ApprovalCallback] = None,
        timeout_sec: int = 15,
        explain_failures: bool = True,
        language: str = "python",
        allow_network: bool = False,
    ):
        self.language = get_language(language).key
        self.allow_network = allow_network
        self.llm = llm_client
        self.sandbox = sandbox
        self.max_retries = max_retries
        self.verbose = verbose
        # Human-in-the-loop gate. If None, every package install is DENIED.
        self.approve_install = approve_install
        self.timeout_sec = timeout_sec
        self.explain_failures = explain_failures

    def _log(self, msg: str):
        if self.verbose:
            print(f"[AutonomousAgent] {msg}")

    # ------------------------------------------------------------------
    def run(self, task: str, initial_code: Optional[str] = None) -> AgentState:
        """
        initial_code: optional script used instead of the LLM's first attempt.
        The eval harness uses it to seed a known runtime error so error recovery
        can be measured deterministically.
        """
        state = AgentState(task=task, max_retries=self.max_retries, language=self.language)
        start = time.time()
        self._log(f"Received task: {task[:70]}...")

        messages = [
            {"role": "system", "content": build_system_prompt(self.language, self.allow_network)},
            {"role": "user", "content": build_user_task_prompt(task)},
        ]
        installed = set()

        for attempt in range(1, self.max_retries + 1):
            self._log(f"--- Iteration {attempt}/{self.max_retries} ---")

            if attempt == 1 and initial_code is not None:
                raw, p_tok, c_tok = f"```{get_language(self.language).fences[0]}\n{initial_code}\n```", 0, 0
            else:
                raw, p_tok, c_tok = self.llm.query(messages)
            messages.append({"role": "assistant", "content": raw})  # keep history for repairs

            code, reqs = parse_response(raw, self.language)
            result: Optional[SandboxResult] = None

            if not code:
                result = SandboxResult("", f"No code block found in the reply. Reply with one complete ```{get_language(self.language).fences[0]} block.", 1, 0.0)
            else:
                new_reqs = [r for r in reqs if _pkg_key(r) not in installed]
                if new_reqs:
                    result = self._handle_install(new_reqs, installed, state)
                if result is None:
                    self._log("Running code in sandbox...")
                    result = self.sandbox.run_code(
                        code, timeout_sec=self.timeout_sec,
                        language=self.language, allow_network=self.allow_network,
                    )

            state.add_iteration(attempt, code, result, p_tok, c_tok, packages=reqs)
            ok = result.exit_code == 0 and not result.timed_out

            if ok:
                self._log(f"Success on attempt {attempt}.")
                state.is_success = True
                state.final_code = code
                state.final_output = result.stdout
                break

            self._log(f"Attempt {attempt} failed (exit {result.exit_code}{', timed out' if result.timed_out else ''}).")
            if attempt == self.max_retries:
                self._log("Max retries reached. Stopping.")
                state.final_code = code
                state.final_error = result.stderr or "Execution failed / timed out"
                break

            messages.append({
                "role": "user",
                "content": build_feedback_prompt(
                    result.stdout, result.stderr, result.exit_code, result.timed_out, attempt, self.max_retries
                ),
            })

        if not state.is_success and self.explain_failures:
            state.final_explanation = self._explain_failure(state)

        state.total_duration = time.time() - start
        return state

    # ------------------------------------------------------------------
    def _handle_install(self, packages: List[str], installed: set, state: AgentState) -> Optional[SandboxResult]:
        """Ask the human, then install. Returns a failure result, or None if OK to proceed."""
        pkg_list = ", ".join(packages)
        if not hasattr(self.sandbox, "install_packages"):
            return SandboxResult("", f"This sandbox cannot install packages ({pkg_list}). Use only the standard library.", 1, 0.0)

        approved = bool(self.approve_install(packages)) if self.approve_install else False
        state.install_requests.append({"packages": list(packages), "approved": approved})
        self._log(f"Package install requested: {pkg_list} -> {'APPROVED' if approved else 'DENIED'}")

        if not approved:
            return SandboxResult("", f"The user did NOT approve installing: {pkg_list}. Rewrite the script using only the Python standard library.", 1, 0.0)

        res = self.sandbox.install_packages(packages)
        if res.exit_code != 0:
            res.stderr = "pip install failed:\n" + res.stderr
            return res
        installed.update(_pkg_key(p) for p in packages)
        return None

    def _explain_failure(self, state: AgentState) -> str:
        lines = []
        for log in state.iterations:
            err = (log.stderr or "").strip()[-600:] or "(no stderr)"
            flag = " [TIMED OUT]" if log.timed_out else ""
            lines.append(f"Attempt {log.attempt}: exit {log.exit_code}{flag}\n{err}")
        try:
            text, p, c = self.llm.query([
                {"role": "system", "content": SYSTEM_EXPLAINER_PROMPT},
                {"role": "user", "content": build_explanation_prompt(state.task, "\n\n".join(lines))},
            ])
            state.total_tokens += p + c
            return text.strip()
        except Exception as e:  # never let the explanation crash a finished run
            return f"(Could not generate explanation: {e})"
