"""
sandbox/terminal.py - Persistent, locked-down terminal session (Docker backend).

* One long-lived container (`sleep infinity`) with NO network. Commands run via `docker exec`, so files,
  compiled programs and installed packages persist between steps.
* Only the workspace folder is mounted (a private temp dir by default). Nothing else from your machine
  is visible. If you pass your own folder, the agent can read/modify/delete everything inside it, so
  point it at a COPY of a project, never at your home directory.
* Commands that need the internet run in a SEPARATE short-lived container that shares the workspace,
  so the long-lived container can never reach the network.
"""

import os
import shutil
import stat
import tempfile
import time
from typing import Dict, List, Optional

from agent.state import SandboxResult

from .docker_runner import _truncate


def _is_link_like(path: str) -> bool:
    """True for symlinks AND Windows reparse points (junctions, WSL/Docker-created links), or if unreadable."""
    try:
        st = os.lstat(path)
    except OSError:
        return True
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


_SKIP_DIRS = {"__pycache__", "node_modules", "site-packages", "packages", "venv", "env", "build"}


def _hidden(name: str) -> bool:
    low = name.lower()
    return low.startswith(".") or low in _SKIP_DIRS or low.endswith((".dist-info", ".egg-info"))


class DockerTerminalSession:
    backend = "docker"

    def __init__(self, runner, workspace: Optional[str] = None, mem_limit: str = "512m",
                 pids_limit: int = 128, max_output_chars: int = 10_000):
        self.runner = runner
        self.client = runner.client
        self.mem_limit = mem_limit
        self.pids_limit = pids_limit
        self.max_output_chars = max_output_chars
        self.owns_workspace = workspace is None
        self.workspace = os.path.abspath(workspace) if workspace else tempfile.mkdtemp(prefix="sandbox_ws_")
        if self.owns_workspace:
            os.chmod(self.workspace, 0o777)
        self.container = None
        self.env = {
            "HOME": "/tmp",
            "PYTHONPATH": "/work/.deps",
            "PIP_TARGET": "/work/.deps",       # `pip install x` lands in the workspace
            "PIP_ONLY_BINARY": ":all:",        # wheels only: no setup.py code runs at install time
            "PIP_NO_CACHE_DIR": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": "/tmp",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TERM": "dumb",
        }

    # ------------------------------------------------------------------
    def start(self):
        self.container = self.client.containers.run(
            image=self.runner.image_name,
            command=["sleep", "infinity"],
            volumes={self.workspace: {"bind": "/work", "mode": "rw"}},
            working_dir="/work",
            environment=self.env,
            mem_limit=self.mem_limit,
            memswap_limit=self.mem_limit,
            nano_cpus=self.runner.nano_cpus,
            pids_limit=self.pids_limit,
            network_disabled=True,             # the long-lived container is ALWAYS offline
            user="65534:65534",
            read_only=True,
            tmpfs={"/tmp": "rw,exec,size=128m"},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            init=True,
            detach=True,
            labels={"sandbox-agent": "terminal", "created": str(int(time.time()))},
        )
        return self

    def run(self, command: str, timeout_sec: int = 30, network: bool = False) -> SandboxResult:
        if network:
            return self._run_networked(command, timeout_sec)
        t0 = time.time()
        try:
            res = self.container.exec_run(
                ["timeout", "-k", "2", str(timeout_sec), "bash", "-c", command],
                workdir="/work", environment=self.env, user="65534:65534", demux=True,
            )
            out, err = res.output if isinstance(res.output, tuple) else (res.output, b"")
            stdout = (out or b"").decode("utf-8", errors="replace")
            stderr = (err or b"").decode("utf-8", errors="replace")
            code = res.exit_code if res.exit_code is not None else 1
            timed_out = code == 124
            if timed_out:
                stderr += f"\nTimeoutExpired: command exceeded {timeout_sec}s and was killed."
            return SandboxResult(_truncate(stdout, self.max_output_chars), _truncate(stderr, self.max_output_chars),
                                 code, time.time() - t0, timed_out)
        except Exception as e:
            return SandboxResult("", f"Terminal error: {e}", 1, time.time() - t0)

    def _run_networked(self, command: str, timeout_sec: int) -> SandboxResult:
        res = self.runner._execute(
            command=["timeout", "-k", "2", str(timeout_sec), "bash", "-c", command],
            volumes={self.workspace: {"bind": "/work", "mode": "rw"}},
            timeout_sec=timeout_sec + 15,
            network_disabled=False,
            working_dir="/work",
            environment=self.env,
            mem_limit="1g",
            tmpfs_size="512m",       # pip needs room to download and unpack wheels
        )
        if res.exit_code == 124:
            res.timed_out = True
        return res

    # ------------------------------------------------------------------
    def _safe_path(self, rel: str) -> Optional[str]:
        """Resolve a workspace-relative path, refusing symlinks that could point at host files."""
        root = os.path.realpath(self.workspace)
        full = os.path.join(root, rel)
        if _is_link_like(full) or os.path.realpath(full) != os.path.normpath(full):
            return None
        return full if os.path.commonpath([root, os.path.realpath(full)]) == root else None

    def list_files(self, max_files: int = 60, max_depth: int = 3) -> List[str]:
        root = os.path.realpath(self.workspace)
        found: List[str] = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            depth = os.path.relpath(dirpath, root).count(os.sep)
            dirnames[:] = [d for d in dirnames if not _hidden(d) and not _is_link_like(os.path.join(dirpath, d))]
            if depth >= max_depth:
                dirnames[:] = []
            for name in filenames:
                full = os.path.join(dirpath, name)
                if not _hidden(name) and not _is_link_like(full):
                    found.append(os.path.relpath(full, root))
                    if len(found) >= max_files:
                        return sorted(found)
        return sorted(found)

    def read_file(self, rel: str, max_bytes: int = 2_000_000) -> Optional[bytes]:
        path = self._safe_path(rel)
        if not path or not os.path.isfile(path) or os.path.getsize(path) > max_bytes:
            return None
        with open(path, "rb") as f:
            return f.read()

    def close(self):
        if self.container is not None:
            try:  # files are owned by the sandbox user, so clean them from inside first
                if self.owns_workspace:
                    self.container.exec_run(["bash", "-c", "rm -rf /work/* /work/.[!.]* 2>/dev/null"], user="65534:65534")
                self.container.remove(force=True)
            except Exception:
                pass
            self.container = None
        if self.owns_workspace:
            shutil.rmtree(self.workspace, ignore_errors=True)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()


def reap_stale_terminals(client, max_age_sec: int = 3600) -> int:
    """Remove terminal containers older than max_age_sec (call on app start; protects free servers)."""
    removed = 0
    for c in client.containers.list(all=True, filters={"label": "sandbox-agent=terminal"}):
        try:
            created = int(c.labels.get("created", "0"))
            if time.time() - created > max_age_sec:
                c.remove(force=True)
                removed += 1
        except Exception:
            pass
    return removed
