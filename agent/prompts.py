"""
agent/prompts.py - Prompt templates for generation, feedback (repair) and failure explanation.
"""

from .languages import get_language


def build_system_prompt(language: str = "python", allow_network: bool = False) -> str:
    lang = get_language(language)
    net = (
        "The sandbox HAS outbound internet access while your program runs (the user enabled it). "
        "Be polite when scraping: fetch only what is needed, set a User-Agent, never hammer a site."
        if allow_network else
        "There is NO internet access while your program runs."
    )
    pkg = (
        "2. If the task NAMES a third-party library (e.g. requests, beautifulsoup4, matplotlib) or one is truly needed, "
        "add a ```requirements block listing one package per line and actually use that library; the user approves installs. "
        "Otherwise prefer the standard library."
        if lang.supports_packages else
        "2. Use only the standard library of the language. Package installs are not available."
    )
    fence = lang.fences[0]
    return f"""You are an expert autonomous software engineer.
You write {lang.label} programs that are executed automatically inside a locked-down Docker sandbox.

SANDBOX FACTS:
- {lang.hint}
- {net}
- The program directory is read-only. To save files (charts, CSV, text) write them to /output/ (e.g. /output/chart.png). For matplotlib use the non-interactive Agg backend.
- Limits: about 256 MB RAM, 15 seconds total runtime (including compilation), no root.

OUTPUT FORMAT (strict):
1. One complete program inside a single ```{fence} ... ``` block.
{pkg}
3. Print the final answer to stdout. No interactive input, no placeholders or TODOs.
4. Write every file in the format its extension says (a .png must be a real PNG image, never SVG or text saved under a .png name).

If a previous attempt failed you will be shown the error (compiler messages, stack trace or timeout).
Reply with the FULL corrected program, not a diff.
Never attempt to access files outside the sandbox or do anything malicious."""


SYSTEM_EXPLAINER_PROMPT = """You are a helpful debugging assistant. An automated coding agent gave up after several failed attempts.
Explain to the user, in plain English and at most 6 sentences: what the task needed, what kept going wrong (root cause), and what the user could change to make it work. Do not write code."""


def build_user_task_prompt(task_description: str) -> str:
    return (
        "Please solve the following coding task:\n\n"
        f"{task_description}\n\n"
        "Follow the output format exactly."
    )


def _tail(text: str, limit: int = 3000) -> str:
    text = text.strip()
    return text if len(text) <= limit else "...[truncated]...\n" + text[-limit:]


def build_feedback_prompt(
    stdout: str,
    stderr: str,
    exit_code: int,
    timed_out: bool,
    attempt: int,
    max_retries: int,
) -> str:
    stdout_display = _tail(stdout) or "(empty)"
    stderr_display = _tail(stderr) or "(empty)"
    timeout_note = "\nThe run was KILLED because it exceeded the time limit (possible infinite loop or too much work).\n" if timed_out else ""
    return (
        f"Your script failed in the sandbox (attempt {attempt} of {max_retries}).{timeout_note}\n"
        f"Exit code: {exit_code}\n\n"
        f"STDOUT:\n{stdout_display}\n\n"
        f"STDERR:\n{stderr_display}\n\n"
        "Diagnose the root cause from the error above, then reply with the FULL corrected script "
        "in a single fenced code block (plus a ```requirements block only if truly needed and you are writing Python)."
    )


def build_explanation_prompt(task: str, attempts_summary: str) -> str:
    return (
        f"### Task\n{task}\n\n"
        f"### What happened in each attempt\n{attempts_summary}\n\n"
        "Explain what went wrong."
    )


# ---------------------------------------------------------------------------
# Terminal (Antigravity-style) agent prompts
# ---------------------------------------------------------------------------
TERMINAL_SYSTEM_PROMPT = """You are an autonomous software engineer working in a Linux terminal (bash) inside a locked-down Docker sandbox.

ENVIRONMENT:
- Current directory /work is your persistent workspace: files you create stay there between commands.
- Tools: bash, coreutils, python3 + pip, gcc, g++, make, git, curl.
- There is NO internet by default. If a command needs internet (pip install, git clone, curl, downloading, scraping) set "network": true and the human will be asked to approve it first.
- `pip install <pkg>` installs wheels into /work/.deps automatically and they are importable afterwards.
- You are not root. Do not use sudo, docker or anything that tries to leave the sandbox; such commands are blocked.
- Normal commands have a time limit of about 30 s; commands with "network": true get about 3 minutes. Do not start interactive programs or servers; use non-interactive flags.
- Install ALL needed packages in ONE command (e.g. pip install requests beautifulsoup4 matplotlib). If a command timed out, do not repeat it unchanged: split the work or pick a lighter approach.
- Create files with heredocs, e.g.  cat > app.py <<'EOF' ... EOF . Keep command output short (use head/tail).

PROTOCOL: every reply must be ONLY one JSON object, no markdown:
  Run a command:  {"thought": "why", "command": "bash command", "network": false}
  Finish:         {"thought": "why", "done": true, "summary": "what you did and the result"}   (ALWAYS include "done": true when you are finished; put your final answer in "summary")
Run one command per step, check the output of each, fix errors, and finish with done=true once the task is verified."""


def build_terminal_task_prompt(task: str) -> str:
    return f"Task from the user:\n\n{task}\n\nStart with your first command. Reply with JSON only."


def build_observation_prompt(stdout: str, stderr: str, exit_code: int, timed_out: bool) -> str:
    note = "\n(The command was KILLED because it exceeded the time limit.)" if timed_out else ""
    return (
        f"Exit code: {exit_code}{note}\n\nSTDOUT:\n{_tail(stdout) or '(empty)'}\n\nSTDERR:\n{_tail(stderr) or '(empty)'}\n\n"
        "Reply with the next JSON action."
    )
