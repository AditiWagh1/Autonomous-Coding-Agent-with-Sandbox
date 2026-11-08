"""
agent/state.py - Dataclasses representing execution attempts and the agent run state.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    duration: float
    timed_out: bool = False
    # Files the script wrote to /output (name -> bytes), e.g. charts or CSVs
    artifacts: Dict[str, bytes] = field(default_factory=dict)


@dataclass
class IterationLog:
    attempt: int
    code_generated: str
    stdout: str
    stderr: str
    exit_code: int
    duration: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    timed_out: bool = False
    packages_requested: List[str] = field(default_factory=list)
    artifacts: Dict[str, bytes] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class AgentState:
    task: str
    max_retries: int
    language: str = "python"
    is_success: bool = False
    final_code: Optional[str] = None
    final_output: Optional[str] = None
    final_error: Optional[str] = None
    final_explanation: Optional[str] = None  # plain-English "what went wrong"
    total_duration: float = 0.0
    total_tokens: int = 0
    iterations: List[IterationLog] = field(default_factory=list)
    install_requests: List[Dict[str, Any]] = field(default_factory=list)

    def add_iteration(
        self,
        attempt: int,
        code: str,
        sandbox_res: SandboxResult,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        packages: Optional[List[str]] = None,
    ):
        log = IterationLog(
            attempt=attempt,
            code_generated=code,
            stdout=sandbox_res.stdout,
            stderr=sandbox_res.stderr,
            exit_code=sandbox_res.exit_code,
            duration=sandbox_res.duration,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            timed_out=sandbox_res.timed_out,
            packages_requested=list(packages or []),
            artifacts=dict(sandbox_res.artifacts),
        )
        self.iterations.append(log)
        self.total_tokens += prompt_tokens + completion_tokens

    # --- metrics used by the evaluation harness -------------------------
    @property
    def first_attempt_failed(self) -> bool:
        return bool(self.iterations) and not self.iterations[0].succeeded

    @property
    def recovered(self) -> bool:
        """True if the first attempt hit an error but a later attempt fixed it."""
        return self.first_attempt_failed and self.is_success

    def summary(self) -> Dict[str, Any]:
        return {
            "success": self.is_success,
            "attempts": len(self.iterations),
            "total_tokens": self.total_tokens,
            "total_duration_sec": round(self.total_duration, 2),
            "final_code": self.final_code,
            "final_output": self.final_output,
            "final_error": self.final_error,
            "final_explanation": self.final_explanation,
            "install_requests": self.install_requests,
        }
