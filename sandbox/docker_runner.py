"""
sandbox/docker_runner.py - Locked-down Docker execution harness.

Two kinds of containers:
  * RUN container     - executes the model's code. No network (by default), read-only
                        filesystem, non-root, no capabilities, CPU/RAM/PID limits, hard timeout.
  * INSTALL container - only used after the human approves a package list. It has network,
                        installs wheels into a private deps folder, then exits. The RUN
                        container mounts that folder read-only, so installed libraries are
                        usable while the model's own code still has no internet.

Nothing from your real filesystem is mounted except throw-away temp folders.
"""

import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

from agent.languages import get_language, shell_command
from agent.state import SandboxResult

try:
    import docker
    from docker.errors import DockerException
except ImportError:  # allows importing validate_packages in tests
    docker = None
    DockerException = Exception

# name, optional [extras], optional version specifier. Must start with a letter/digit,
# so options such as "--index-url=..." can never be smuggled into pip.
_PKG_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,_-]+\])?((==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*+!_-]+)?$"
)


def validate_packages(packages: List[str]) -> Optional[str]:
    """Return an error message if any package spec is unsafe/invalid, else None."""
    if not packages:
        return "No packages given."
    if len(packages) > 10:
        return "Too many packages requested (max 10)."
    for p in packages:
        if not _PKG_RE.match(p):
            return f"Invalid or unsafe package specifier: {p!r}"
    return None


DEFAULT_IMAGE = "sandbox-agent:latest"
BUILD_CONTEXT = Path(__file__).resolve().parent.parent / "docker"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n...[output truncated]...\n" + text[-half:]


class DockerSandboxRunner:
    def __init__(
        self,
        image_name: str = DEFAULT_IMAGE,
        mem_limit: str = "256m",
        nano_cpus: int = 1_000_000_000,   # 1.0 CPU
        network_disabled: bool = True,    # network for the RUN container
        pids_limit: int = 64,             # blocks fork bombs
        max_output_chars: int = 10_000,
        only_binary: bool = True,         # wheels only: no setup.py code execution on install
    ):
        if docker is None:
            raise RuntimeError("docker SDK not installed. Run: pip install docker")
        try:
            self.client = docker.from_env()
            self.client.ping()
        except DockerException as e:
            raise RuntimeError(f"Cannot reach Docker. Is Docker Desktop running? ({e})")

        self.image_name = image_name
        self.backend = "docker"
        self.mem_limit = mem_limit
        self.nano_cpus = nano_cpus
        self.network_disabled = network_disabled
        self.pids_limit = pids_limit
        self.max_output_chars = max_output_chars
        self.only_binary = only_binary

        self._ensure_image()
        self.deps_dir = tempfile.mkdtemp(prefix="sandbox_deps_")
        os.chmod(self.deps_dir, 0o777)
        self.installed: List[str] = []

    # ------------------------------------------------------------------
    def _ensure_image(self):
        """Build (our own image) or pull (anything else) the sandbox image if it is missing."""
        try:
            self.client.images.get(self.image_name)
            return
        except docker.errors.ImageNotFound:
            pass
        if self.image_name == DEFAULT_IMAGE:
            print("[sandbox] Building the sandbox image (first run only, a few minutes)...")
            self.client.images.build(path=str(BUILD_CONTEXT), dockerfile="Dockerfile.sandbox", tag=DEFAULT_IMAGE, rm=True)
        else:
            print(f"[sandbox] Pulling image {self.image_name}...")
            self.client.images.pull(self.image_name)

    # ------------------------------------------------------------------
    def close(self):
        shutil.rmtree(self.deps_dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------
    def _execute(
        self,
        command: List[str],
        volumes: Dict[str, dict],
        timeout_sec: int,
        network_disabled: bool,
        working_dir: str,
        environment: Dict[str, str],
        mem_limit: str,
        tmpfs_size: str = "64m",
    ) -> SandboxResult:
        t0 = time.time()
        container = None
        try:
            container = self.client.containers.run(
                image=self.image_name,
                command=command,
                volumes=volumes,
                working_dir=working_dir,
                environment=environment,
                mem_limit=mem_limit,
                memswap_limit=mem_limit,          # no swap -> hard RAM ceiling
                nano_cpus=self.nano_cpus,
                pids_limit=self.pids_limit,
                network_disabled=network_disabled,
                user="65534:65534",               # nobody, never root
                read_only=True,                   # immutable root filesystem
                tmpfs={"/tmp": f"rw,exec,size={tmpfs_size}"},  # exec: compiled C/C++ binaries run from here
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                detach=True,
                labels={"sandbox-agent": "run"},
                # public resolvers: cloud-internal DNS (e.g. 169.254.169.254) stays blocked by the firewall script
                **({} if network_disabled else {"dns": ["1.1.1.1", "8.8.8.8"]}),
            )

            try:
                res = container.wait(timeout=timeout_sec)
            except Exception:
                container.kill()
                return SandboxResult(
                    stdout="",
                    stderr=f"TimeoutExpired: execution exceeded the {timeout_sec}s limit and was killed.",
                    exit_code=-1,
                    duration=float(timeout_sec),
                    timed_out=True,
                )

            exit_code = res.get("StatusCode", 0)
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

            try:
                container.reload()
                if container.attrs.get("State", {}).get("OOMKilled"):
                    stderr += f"\nKilled: script exceeded the {mem_limit} memory limit."
            except Exception:
                pass

            return SandboxResult(
                stdout=_truncate(stdout, self.max_output_chars),
                stderr=_truncate(stderr, self.max_output_chars),
                exit_code=exit_code,
                duration=time.time() - t0,
            )
        except Exception as e:
            return SandboxResult("", f"Sandbox error: {e}", 1, time.time() - t0)
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    def install_packages(self, packages: List[str], timeout_sec: int = 180) -> SandboxResult:
        """Call ONLY after the human approved. Installs into the private deps folder."""
        err = validate_packages(packages)
        if err:
            return SandboxResult("", err, 1, 0.0)

        cmd = ["pip", "install", "--no-cache-dir", "--disable-pip-version-check", "--target", "/deps"]
        if self.only_binary:
            cmd.append("--only-binary=:all:")
        cmd += packages

        res = self._execute(
            command=cmd,
            volumes={self.deps_dir: {"bind": "/deps", "mode": "rw"}},
            timeout_sec=timeout_sec,
            network_disabled=False,               # the ONLY place the network is on
            working_dir="/tmp",
            environment={"HOME": "/tmp"},
            mem_limit="1g",          # tmpfs counts against memory, so give installs headroom
            tmpfs_size="512m",       # wheels are downloaded and unpacked here (64m failed with Errno 28)
        )
        if res.exit_code == 0:
            self.installed.extend(packages)
        return res

    # ------------------------------------------------------------------
    def run_code(self, code: str, timeout_sec: int = 15, language: str = "python",
                 allow_network: Optional[bool] = None) -> SandboxResult:
        """Compile (if needed) and run `code`. allow_network overrides the runner default for this run."""
        lang = get_language(language)
        net_disabled = self.network_disabled if allow_network is None else (not allow_network)
        with tempfile.TemporaryDirectory() as script_dir, tempfile.TemporaryDirectory() as out_dir:
            os.chmod(script_dir, 0o755)
            os.chmod(out_dir, 0o777)              # sandbox user must be able to write results
            script_path = os.path.join(script_dir, lang.filename)
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)
            os.chmod(script_path, 0o644)

            result = self._execute(
                command=["sh", "-c", shell_command(lang, f"/workspace/{lang.filename}")],
                volumes={
                    script_dir: {"bind": "/workspace", "mode": "ro"},
                    out_dir: {"bind": "/output", "mode": "rw"},
                    self.deps_dir: {"bind": "/deps", "mode": "ro"},
                },
                timeout_sec=timeout_sec,
                network_disabled=net_disabled,
                working_dir="/workspace",
                environment={
                    "PYTHONPATH": "/deps",
                    "HOME": "/tmp",
                    "MPLCONFIGDIR": "/tmp",
                    "MPLBACKEND": "Agg",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                mem_limit=self.mem_limit,
            )
            result.artifacts = self._collect_artifacts(out_dir)
            return result

    @staticmethod
    def _collect_artifacts(out_dir: str, max_files: int = 10, max_bytes: int = 5_000_000) -> Dict[str, bytes]:
        found: Dict[str, bytes] = {}
        for name in sorted(os.listdir(out_dir))[:max_files]:
            path = os.path.join(out_dir, name)
            if os.path.isfile(path) and not os.path.islink(path) and os.path.getsize(path) <= max_bytes:
                with open(path, "rb") as f:
                    found[name] = f.read()
        return found
