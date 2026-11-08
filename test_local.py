"""
test_local.py - Offline unit tests (no API key, no Docker needed).
Run:  python test_local.py
"""

from agent import AutonomousCodingAgent, SandboxResult
from agent.parsing import parse_response
from sandbox.docker_runner import validate_packages


class FakeLLM:
    """Returns scripted replies; records the messages it was given."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def query(self, messages, temperature=0.2, json_mode=False):
        self.calls.append([dict(m) for m in messages])
        return self.replies.pop(0), 10, 5


class ScriptedSandbox:
    def __init__(self, results, install_result=None):
        self.results = list(results)
        self.install_result = install_result or SandboxResult("", "", 0, 0.1)
        self.ran, self.installs = [], []

    def run_code(self, code, timeout_sec=15, language="python", allow_network=False):
        self.ran.append((code, language, allow_network))
        return self.results.pop(0)

    def install_packages(self, packages):
        self.installs.append(packages)
        return self.install_result


OK = lambda out="ok\n": SandboxResult(out, "", 0, 0.1)
ERR = lambda msg="NameError: x": SandboxResult("", msg, 1, 0.1)
py = lambda c: f"```python\n{c}\n```"


def test_parsing():
    code, reqs = parse_response("hi\n```python\nprint(1)\n```\n```requirements\nnumpy\nrequests>=2  # http\n```")
    assert code == "print(1)" and reqs == ["numpy", "requests>=2"], (code, reqs)
    assert parse_response("no code here") == ("", [])


def test_package_validation():
    assert validate_packages(["numpy", "requests>=2.0", "pandas[excel]==2.2.1"]) is None
    assert validate_packages(["--index-url=http://evil"]) is not None
    assert validate_packages(["numpy; rm -rf /"]) is not None
    assert validate_packages(["a b"]) is not None
    assert validate_packages([]) is not None


def test_first_try_success():
    s = AutonomousCodingAgent(FakeLLM([py("print(1)")]), ScriptedSandbox([OK("1\n")]), verbose=False).run("t")
    assert s.is_success and len(s.iterations) == 1 and not s.recovered and s.total_tokens == 15


def test_recovery_keeps_history():
    llm = FakeLLM([py("print(x)"), py("print(1)")])
    s = AutonomousCodingAgent(llm, ScriptedSandbox([ERR(), OK("1\n")]), verbose=False).run("t")
    assert s.is_success and s.recovered and len(s.iterations) == 2
    second_call = llm.calls[1]
    assert [m["role"] for m in second_call] == ["system", "user", "assistant", "user"]
    assert "NameError" in second_call[-1]["content"]


def test_stops_after_max_and_explains():
    llm = FakeLLM([py("a"), py("b"), py("c"), "Plain-English explanation."])
    s = AutonomousCodingAgent(llm, ScriptedSandbox([ERR(), ERR(), ERR()]), max_retries=3, verbose=False).run("t")
    assert not s.is_success and len(s.iterations) == 3
    assert s.final_explanation == "Plain-English explanation."
    assert "NameError" in s.final_error


def test_timeout_is_failure():
    t = SandboxResult("", "TimeoutExpired", -1, 15, timed_out=True)
    llm = FakeLLM([py("while True: pass"), py("print(1)")])
    s = AutonomousCodingAgent(llm, ScriptedSandbox([t, OK()]), verbose=False).run("t")
    assert s.is_success and "KILLED" in llm.calls[1][-1]["content"]


def test_install_denied_by_default():
    reply = py("import numpy") + "\n```requirements\nnumpy\n```"
    llm = FakeLLM([reply, py("print(1)")])
    sb = ScriptedSandbox([OK()])
    s = AutonomousCodingAgent(llm, sb, verbose=False).run("t")  # no approval callback
    assert sb.installs == [] and s.install_requests == [{"packages": ["numpy"], "approved": False}]
    assert s.is_success and "did NOT approve" in llm.calls[1][-1]["content"]


def test_install_approved():
    reply = py("import numpy") + "\n```requirements\nnumpy\n```"
    sb = ScriptedSandbox([OK()])
    s = AutonomousCodingAgent(FakeLLM([reply]), sb, verbose=False, approve_install=lambda p: True).run("t")
    assert sb.installs == [["numpy"]] and s.is_success


def test_seeded_initial_code():
    llm = FakeLLM([py("print(2)")])
    s = AutonomousCodingAgent(llm, ScriptedSandbox([ERR(), OK("2\n")]), verbose=False).run("t", initial_code="print(x)")
    assert s.iterations[0].code_generated == "print(x)" and s.iterations[0].prompt_tokens == 0
    assert s.recovered and len(llm.calls) == 1


def test_language_and_network_passthrough():
    llm = FakeLLM(["```c\nint main(){return 0;}\n```"])
    sb = ScriptedSandbox([OK()])
    s = AutonomousCodingAgent(llm, sb, verbose=False, language="c", allow_network=True).run("t")
    assert s.is_success and sb.ran[0][1:] == ("c", True) and s.language == "c"
    assert "C (gcc" in llm.calls[0][0]["content"] and "HAS outbound internet" in llm.calls[0][0]["content"]


def test_cpp_fence_aliases_and_no_requirements_for_c():
    code, reqs = parse_response("```c++\nint main(){}\n```\n```requirements\nnumpy\n```", "cpp")
    assert code == "int main(){}" and reqs == []
    assert parse_response("```python\nprint(1)\n```", "c") == ("", [])


def test_missing_code_block():
    llm = FakeLLM(["just prose", py("print(1)")])
    s = AutonomousCodingAgent(llm, ScriptedSandbox([OK()]), verbose=False).run("t")
    assert s.is_success and s.iterations[0].exit_code == 1


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
    print(f"\nAll {len(tests)} offline tests passed.")
