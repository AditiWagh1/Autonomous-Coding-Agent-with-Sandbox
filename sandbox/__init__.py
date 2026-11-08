"""Sandbox backends. Imports are lazy so a missing docker/e2b package only fails when that backend is used."""

from .factory import create_runner, create_terminal, backend_name


def __getattr__(name):  # keep `from sandbox import DockerSandboxRunner` working
    if name in ("DockerSandboxRunner", "validate_packages"):
        from . import docker_runner
        return getattr(docker_runner, name)
    if name in ("DockerTerminalSession", "reap_stale_terminals"):
        from . import terminal
        return getattr(terminal, name)
    raise AttributeError(name)


__all__ = ["create_runner", "create_terminal", "backend_name", "DockerSandboxRunner",
           "validate_packages", "DockerTerminalSession", "reap_stale_terminals"]
