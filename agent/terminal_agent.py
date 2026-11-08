"""
agent/terminal_agent.py - Antigravity-style terminal agent.

The model controls a bash terminal inside a Docker sandbox one command at a time:
    propose -> (policy check / human approval) -> execute -> observe -> repeat
It can be driven step-by-step (Streamlit buttons) or all at once with run() (CLI / evals).
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .policy import Decision, classify
from .prompts import (
    SYSTEM_EXPLAINER_PROMPT,
    TERMINAL_SYSTEM_PROMPT,
    build_explanation_prompt,
    build_observation_prompt,
    build_terminal_task_prompt,
)


@dataclass
class Action:
    thought: str = ""
    command: Optional[str] = None
    network: bool = False
    done: bool = False
    summary: str = ""


@dataclass
class PendingStep:
    step: int
    action: Action
    decision: Decision


@dataclass
class StepLog:
    step: int
    thought: str
    command: str
    network: bool
    outcome: str            # "ran" | "denied" | "blocked"
    reason: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    duration: float = 0.0
    timed_out: bool = False


@dataclass
class TerminalState:
    task: str
    max_steps: int
    steps: List[StepLog] = field(default_factory=list)
    finished: bool = False
    success: bool = False
    summary: str = ""
    stop_reason: str = ""
    explanation: Optional[str] = None
    total_tokens: int = 0
    invalid_replies: int = 0
    invalid_samples: List[str] = field(default_factory=list)
    consecutive_timeouts: int = 0
    duration: float = 0.0


def parse_action(raw: str) -> Optional[Action]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        obj = json.loads(text)
    except ValueError:
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e <= s:
            return None
        try:
            obj = json.loads(text[s:e + 1])
        except ValueError:
            return None
    if not isinstance(obj, dict):
        return None
    done = bool(obj.get("done"))
    cmd = obj.get("command")
    has_cmd = isinstance(cmd, str) and cmd.strip()
    final_text = ""
    for key in ("summary", "answer", "result", "response", "final_answer", "message"):
        if isinstance(obj.get(key), str) and obj[key].strip():
            final_text = obj[key].strip()
            break
    if not done and not has_cmd:
        if not final_text:
            return None
        done = True  # small models often forget "done": a final answer with no command means finished
    return Action(
        thought=str(obj.get("thought", "")),
        command=cmd.strip() if has_cmd else None,
        network=bool(obj.get("network")),
        done=done,
        summary=final_text,
    )


class TerminalAgent:
    def __init__(self, llm, session, max_steps: int = 15, approval_mode: str = "risky",
                 cmd_timeout: int = 30, network_timeout: int = 180, explain_failures: bool = True, max_invalid: int = 3):
        """approval_mode: 'risky' = ask only for network/install commands, 'all' = ask for every command."""
        self.llm = llm
        self.session = session
        self.max_steps = max_steps
        self.approval_mode = approval_mode
        self.cmd_timeout = cmd_timeout
        self.network_timeout = network_timeout   # installs/downloads are slow: matplotlib alone can take > 1 min
        self.explain_failures = explain_failures
        self.max_invalid = max_invalid
        self.state = TerminalState(task="", max_steps=max_steps)
        self.messages: List[dict] = []
        self._recent: List[str] = []
        self._rejections = 0
        self._t0 = time.time()

    # ------------------------------------------------------------------
    def start(self, task: str):
        self.state = TerminalState(task=task, max_steps=self.max_steps)
        self.messages = [
            {"role": "system", "content": TERMINAL_SYSTEM_PROMPT},
            {"role": "user", "content": build_terminal_task_prompt(task)},
        ]
        self._recent, self._rejections, self._t0 = [], 0, time.time()

    def propose(self) -> Optional[PendingStep]:
        """Ask the model for the next action. Returns None when the run has finished."""
        st = self.state
        if st.finished:
            return None
        if len(st.steps) >= self.max_steps:
            self._finish(False, "Reached the maximum number of steps")
            return None

        raw, p, c = self.llm.query(self.messages, json_mode=True)
        st.total_tokens += p + c
        self.messages.append({"role": "assistant", "content": raw})

        action = parse_action(raw)
        if action is None:
            st.invalid_replies += 1
            st.invalid_samples.append((raw or "")[:400])
            if st.invalid_replies >= self.max_invalid:
                self._finish(False, "The model kept returning invalid JSON")
                return None
            self.messages.append({"role": "user", "content": (
                "Your reply was not a valid action. Reply with ONLY one JSON object: "
                '{"thought": "...", "command": "...", "network": false} or {"thought": "...", "done": true, "summary": "..."}')})
            return self.propose()

        if action.done:
            self._finish(True, "Model reported the task as complete", action.summary)
            return None

        return PendingStep(step=len(st.steps) + 1, action=action,
                           decision=classify(action.command, action.network, self.approval_mode))

    def execute(self, pending: PendingStep, approved: bool) -> StepLog:
        a, d, st = pending.action, pending.decision, self.state
        result = None
        if d.kind == "block":
            outcome, feedback = "blocked", f"BLOCKED by the safety policy ({d.reason}). Choose a different approach."
        elif d.kind == "ask" and not approved:
            outcome, feedback = "denied", "The user DENIED this command. Do not retry it; choose a different approach or finish."
        else:
            outcome, feedback = "ran", ""
            result = self.session.run(a.command, timeout_sec=self.network_timeout if d.network else self.cmd_timeout, network=d.network)

        log = StepLog(
            step=pending.step, thought=a.thought, command=a.command or "", network=d.network,
            outcome=outcome, reason=d.reason,
            stdout=result.stdout if result else "", stderr=result.stderr if result else "",
            exit_code=result.exit_code if result else None,
            duration=result.duration if result else 0.0,
            timed_out=result.timed_out if result else False,
        )
        st.steps.append(log)
        self.messages.append({"role": "user", "content": feedback or build_observation_prompt(
            log.stdout, log.stderr, log.exit_code or 0, log.timed_out)})

        self._rejections = self._rejections + 1 if outcome != "ran" else 0
        st.consecutive_timeouts = st.consecutive_timeouts + 1 if log.timed_out else 0
        self._recent.append(a.command or "")
        if len(self._recent) >= 3 and len(set(self._recent[-3:])) == 1:
            self._finish(False, "The agent got stuck repeating the same command")
        elif st.consecutive_timeouts >= 3:
            self._finish(False, "Three commands in a row timed out")
        elif self._rejections >= 3:
            self._finish(False, "Too many blocked or denied commands in a row")
        elif len(st.steps) >= self.max_steps:
            self._finish(False, "Reached the maximum number of steps")
        return log

    # ------------------------------------------------------------------
    def run(self, task: str, approve: Optional[Callable[[PendingStep], bool]] = None) -> TerminalState:
        """Drive the whole loop. `approve` is asked for every 'ask' step; without it those steps are denied."""
        self.start(task)
        while True:
            pending = self.propose()
            if pending is None:
                break
            if pending.decision.kind == "ask":
                ok = bool(approve(pending)) if approve else False
            else:
                ok = pending.decision.kind == "auto"
            self.execute(pending, ok)
            if self.state.finished:
                break
        return self.state

    def _finish(self, success: bool, reason: str, summary: str = ""):
        st = self.state
        st.finished, st.success, st.stop_reason, st.summary = True, success, reason, summary
        st.duration = time.time() - self._t0
        if not success and self.explain_failures:
            st.explanation = self._explain()

    def _explain(self) -> str:
        lines = []
        for s in self.state.steps[-8:]:
            tail = (s.stderr or s.stdout or "").strip()[-300:]
            lines.append(f"Step {s.step} [{s.outcome}] $ {s.command}\n-> exit {s.exit_code}: {tail}")
        lines.append(f"Stopped because: {self.state.stop_reason}")
        try:
            text, p, c = self.llm.query([
                {"role": "system", "content": SYSTEM_EXPLAINER_PROMPT},
                {"role": "user", "content": build_explanation_prompt(self.state.task, "\n\n".join(lines))},
            ])
            self.state.total_tokens += p + c
            return text.strip()
        except Exception as e:
            return f"(Could not generate explanation: {e})"
