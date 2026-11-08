"""
sandbox/e2b_backend.py - Cloud sandbox backend using E2B (Firecracker micro-VMs).

Use this when you deploy somewhere that cannot run Docker containers (Render, Railway, Hugging Face
Spaces ...). The app runs on the host; model-written code runs in a separate E2B micro-VM, so there is
no Docker socket and no privileged container anywhere in your deployment.

Setup:  pip install e2b      and set E2B_API_KEY  (free Hobby plan, no card)   -> https://e2b.dev

Differences from the Docker backend (be honest about them in your report):
  * Internet access is decided when the sandbox is created (allow_internet), not per command.
    Approval prompts still gate installs/network-looking commands, but this is a policy layer here,
    whereas the Docker backend physically separates offline and online execution.
  * Memory/CPU/time limits come from E2B's template and plan, not from this code.
NOTE: written against the E2B v2 Python SDK (Sandbox.create / commands.run / files.write). If your
installed version differs, adjust the few SDK calls marked [SDK].
"""

import os
import shlex
import time
from typing import List, Optional

from agent.languages import get_language, shell_command
from agent.state import SandboxResult

from .docker_runner import _truncate, validate_packages

WORK = "/home/user/work"
OUT = "/home/user/output"


class _E2BBase:
    def __init__(self, allow_internet: bool = False, lifetime_sec: int = 900, max_output_chars: int = 10_000):
        try:
            from e2b import Sandbox  # [SDK]
        except ImportError:
            raise RuntimeError("e2b is not installed. Run: pip install e2b")
        if not os.getenv("E2B_API_KEY"):
            raise RuntimeError("E2B_API_KEY is not set (get a free key at https://e2b.dev).")
        self._Sandbox = Sandbox
        self.allow_internet = allow_internet
        self.lifetime_sec = lifetime_sec
        self.max_output_chars = max_output_chars
        self._sbx = None

    @property
    def sbx(self):
        if self._sbx is None:
            self._sbx = self._Sandbox.create(timeout=self.lifetime_sec, allow_internet_access=self.allow_internet)  # [SDK]
            self._sbx.commands.run(f"mkdir -p {WORK} {OUT} && chmod 777 {OUT}", user="root")  # [SDK]
        return self._sbx

    def _cmd(self, command: str, timeout_sec: int, cwd: str = WORK) -> SandboxResult:
        t0 = time.time()
        try:
            r = self.sbx.commands.run(command, cwd=cwd, timeout=timeout_sec)  # [SDK]
            return SandboxResult(_truncate(r.stdout or "", self.max_output_chars),
                                 _truncate(r.stderr or "", self.max_output_chars), r.exit_code, time.time() - t0)
        except Exception as e:
            if hasattr(e, "exit_code"):  # SDK raises on non-zero exit
                return SandboxResult(_truncate(getattr(e, "stdout", "") or "", self.max_output_chars),
                                     _truncate(getattr(e, "stderr", "") or str(e), self.max_output_chars),
                                     e.exit_code, time.time() - t0)
            if "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower():
                return SandboxResult("", f"TimeoutExpired: exceeded {timeout_sec}s.", -1, float(timeout_sec), True)
            return SandboxResult("", f"E2B sandbox error: {e}", 1, time.time() - t0)

    def close(self):
        if self._sbx is not None:
            try:
                self._sbx.kill()  # [SDK]
            except Exception:
                pass
            self._sbx = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class E2BSandboxRunner(_E2BBase):
    backend = "e2b"

    def run_code(self, code: str, timeout_sec: int = 15, language: str = "python",
                 allow_network: Optional[bool] = None) -> SandboxResult:
        lang = get_language(language)
        src = f"{WORK}/{lang.filename}"
        try:
            self.sbx.commands.run(f"rm -rf {OUT}/* {WORK}/prog", user="root")  # [SDK]
            self.sbx.files.write(src, code)  # [SDK]
        except Exception as e:
            return SandboxResult("", f"E2B sandbox error: {e}", 1, 0.0)
        res = self._cmd(shell_command(lang, src, f"{WORK}/prog"), timeout_sec)
        res.artifacts = self._collect(OUT)
        return res

    def install_packages(self, packages: List[str], timeout_sec: int = 180) -> SandboxResult:
        err = validate_packages(packages)
        if err:
            return SandboxResult("", err, 1, 0.0)
        if not self.allow_internet:
            return SandboxResult("", "This E2B sandbox was created without internet access, so packages cannot be installed. "
                                     "Enable 'Allow internet' (or pre-approve packages) before starting.", 1, 0.0)
        return self._cmd("pip install --only-binary=:all: " + " ".join(shlex.quote(p) for p in packages), timeout_sec)

    def _collect(self, directory: str, max_files: int = 10, max_bytes: int = 5_000_000):
        found = {}
        try:
            names = self.sbx.commands.run(f"ls -1p {directory} | grep -v / | head -n {max_files}").stdout.split()  # [SDK]
            for name in names:
                data = self.sbx.files.read(f"{directory}/{name}", format="bytes")  # [SDK]
                if len(data) <= max_bytes:
                    found[name] = bytes(data)
        except Exception:
            pass
        return found


class E2BTerminalSession(_E2BBase):
    backend = "e2b"

    def __init__(self, runner: Optional[E2BSandboxRunner] = None, allow_internet: bool = False, **kw):
        super().__init__(allow_internet=runner.allow_internet if runner else allow_internet, **kw)

    def start(self):
        _ = self.sbx
        return self

    def run(self, command: str, timeout_sec: int = 30, network: bool = False) -> SandboxResult:
        if network and not self.allow_internet:
            return SandboxResult("", "This sandbox has no internet access (it was created offline).", 1, 0.0)
        return self._cmd(f"timeout -k 2 {int(timeout_sec)} bash -c {shlex.quote(command)}", timeout_sec + 10)

    def list_files(self, max_files: int = 60, max_depth: int = 3):
        r = self._cmd(f"find . -maxdepth {max_depth} -type f -not -path '*/.*' -not -path '*/node_modules/*' -not -path '*/site-packages/*' -not -path '*/packages/*' -not -path '*dist-info*' | head -n {max_files}", 15)
        return sorted(p[2:] for p in r.stdout.split("\n") if p.startswith("./"))

    def read_file(self, rel: str, max_bytes: int = 2_000_000):
        try:
            data = self.sbx.files.read(f"{WORK}/{rel}", format="bytes")  # [SDK]
            return bytes(data) if len(data) <= max_bytes else None
        except Exception:
            return None
