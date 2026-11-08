"""
sandbox/factory.py - Pick the execution backend: SANDBOX_BACKEND=docker (default) or e2b.
"""

import os


def backend_name(explicit=None) -> str:
    return (explicit or os.getenv("SANDBOX_BACKEND", "docker")).strip().lower()


def create_runner(backend=None, mem_limit: str = "256m", allow_network: bool = False):
    name = backend_name(backend)
    if name == "e2b":
        from .e2b_backend import E2BSandboxRunner
        return E2BSandboxRunner(allow_internet=allow_network)
    if name == "docker":
        from .docker_runner import DockerSandboxRunner
        return DockerSandboxRunner(mem_limit=mem_limit, network_disabled=True)
    raise ValueError(f"Unknown SANDBOX_BACKEND {name!r} (use 'docker' or 'e2b')")


def create_terminal(runner, workspace=None):
    """Terminal session matching the runner's backend. The session must be .start()ed (or used in `with`)."""
    if getattr(runner, "backend", "docker") == "e2b":
        from .e2b_backend import E2BTerminalSession
        return E2BTerminalSession(runner=runner)
    from .terminal import DockerTerminalSession
    return DockerTerminalSession(runner, workspace=workspace)
