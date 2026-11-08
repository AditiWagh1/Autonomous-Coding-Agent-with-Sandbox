"""
evals/security_tests.py - Proves the sandbox contains hostile code. No LLM / API key needed.
Run from the project root:   python -m evals.security_tests
Needs Docker Desktop running.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sandbox import DockerSandboxRunner, DockerTerminalSession  # noqa: E402

TESTS = [
    ("Infinite loop is killed by timeout",
     "while True:\n    pass",
     lambda r: r.timed_out),

    ("Memory bomb is killed (256 MB cap)",
     "x = bytearray(2 * 1024**3)\nprint('allocated')",
     lambda r: r.exit_code != 0 and "allocated" not in r.stdout),

    ("No outbound network",
     "import urllib.request\nurllib.request.urlopen('http://example.com', timeout=4)\nprint('REACHED')",
     lambda r: "REACHED" not in r.stdout and r.exit_code != 0),

    ("Host files are invisible",
     "import os\n"
     "paths = ['/Users', '/home', '/root/.ssh', '/mnt/c', '/host_mnt', os.path.expanduser('~/.ssh')]\n"
     "leak = [p for p in paths if os.path.exists(p) and os.listdir(p)]\n"
     "print('LEAK', leak) if leak else print('ISOLATED')",
     lambda r: "ISOLATED" in r.stdout),

    ("Script directory is read-only",
     "try:\n    open('/workspace/evil.txt', 'w').write('x'); print('WROTE')\nexcept OSError:\n    print('READONLY')",
     lambda r: "READONLY" in r.stdout),

    ("Root filesystem is read-only",
     "try:\n    open('/etc/evil', 'w').write('x'); print('WROTE')\nexcept OSError:\n    print('READONLY')",
     lambda r: "READONLY" in r.stdout),

    ("Fork bomb is capped by PID limit",
     "import os, time\n"
     "try:\n"
     "    for _ in range(500):\n"
     "        if os.fork() == 0:\n"
     "            time.sleep(5); os._exit(0)\n"
     "    print('UNLIMITED')\n"
     "except OSError:\n"
     "    print('LIMITED')",
     lambda r: "LIMITED" in r.stdout),

    ("Runs as non-root",
     "import os\nprint('ROOT' if os.getuid() == 0 else 'NONROOT')",
     lambda r: "NONROOT" in r.stdout),

    ("C compiles and runs",
     '#include <stdio.h>\nint main(void){printf("C_OK\\n");return 0;}',
     lambda r: "C_OK" in r.stdout, "c"),

    ("C infinite loop is killed by timeout",
     "int main(void){ for(;;){} }",
     lambda r: r.timed_out, "c"),

    ("C++ memory bomb is killed",
     "#include <cstdlib>\n#include <cstring>\nint main(){ for(;;){ char*p=(char*)malloc(1<<24); if(!p) return 3; memset(p,1,1<<24);} }",
     lambda r: r.exit_code != 0, "cpp"),

    ("Output can be saved to /output",
     "open('/output/hello.txt', 'w').write('hi')\nprint('saved')",
     lambda r: "hello.txt" in r.artifacts),
]


TERMINAL_TESTS = [
    ("Terminal: files persist between commands",
     ["echo persisted > note.txt", "cat note.txt"],
     lambda rs, t: "persisted" in rs[-1].stdout),
    ("Terminal: offline by default",
     ["python -c \"import urllib.request as u; u.urlopen('http://example.com', timeout=4); print('REACHED')\""],
     lambda rs, t: "REACHED" not in rs[-1].stdout and rs[-1].exit_code != 0),
    ("Terminal: host files invisible",
     ["python -c \"import os; print('LEAK' if any(os.path.exists(p) and os.listdir(p) for p in ['/Users','/home','/host_mnt','/mnt/c']) else 'ISOLATED')\""],
     lambda rs, t: "ISOLATED" in rs[-1].stdout),
    ("Terminal: root filesystem is read-only",
     ["touch /etc/evil 2>&1 || echo READONLY"],
     lambda rs, t: "READONLY" in rs[-1].stdout),
    ("Terminal: symlinks cannot expose host files",
     ["ln -s /etc/passwd leak.txt; ls -la leak.txt"],
     lambda rs, t: t.read_file("leak.txt") is None and "leak.txt" not in t.list_files()),
    ("Terminal: runaway command is killed",
     ["sleep 30"],
     lambda rs, t: rs[-1].timed_out),
]


if __name__ == "__main__":
    failures = total = 0
    with DockerSandboxRunner(mem_limit="256m", network_disabled=True) as sb:
        for item in TESTS:
            name, code, check, *rest = item
            lang = rest[0] if rest else "python"
            res = sb.run_code(code, timeout_sec=10, language=lang)
            ok = bool(check(res))
            total += 1
            failures += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {name}")
            if not ok:
                print(f"      exit={res.exit_code} timed_out={res.timed_out}\n      stdout={res.stdout[:200]!r}\n      stderr={res.stderr[:300]!r}")

        for name, commands, check in TERMINAL_TESTS:
            with DockerTerminalSession(sb) as term:
                results = [term.run(c, timeout_sec=5) for c in commands]
                ok = bool(check(results, term))
            total += 1
            failures += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {name}")
            if not ok:
                r = results[-1]
                print(f"      exit={r.exit_code} timed_out={r.timed_out}\n      stdout={r.stdout[:200]!r}\n      stderr={r.stderr[:300]!r}")

    print(f"\n{total - failures}/{total} security checks passed")
    sys.exit(1 if failures else 0)
