"""
test_terminal.py - Offline tests for the command policy and the terminal agent loop.
Run:  python test_terminal.py     (no Docker, no API key)
"""

import json

from agent.policy import classify
from agent.state import SandboxResult
from agent.terminal_agent import TerminalAgent, parse_action
from agent.languages import get_language, shell_command


class FakeLLM:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def query(self, messages, temperature=0.2, json_mode=False):
        self.calls.append([dict(m) for m in messages])
        return self.replies.pop(0), 10, 5


class FakeSession:
    def __init__(self, results=None):
        self.results, self.ran, self.timeouts = list(results or []), [], []

    def run(self, command, timeout_sec=30, network=False):
        self.ran.append((command, network))
        self.timeouts.append(timeout_sec)
        return self.results.pop(0) if self.results else SandboxResult("ok\n", "", 0, 0.1)


cmd = lambda c, net=False: json.dumps({"thought": "t", "command": c, "network": net})
done = json.dumps({"thought": "t", "done": True, "summary": "all good"})


def test_policy():
    assert classify("ls -la").kind == "auto"
    assert classify("python main.py").kind == "auto"
    assert classify("pip install requests").kind == "ask" and classify("pip install requests").network
    assert classify("curl https://example.com").kind == "ask"
    assert classify("echo hi", network_flag=True).kind == "ask"
    assert classify("ls", mode="all").kind == "ask" and not classify("ls", mode="all").network
    for bad in ["sudo rm x", "docker ps", "rm -rf /", "rm -rf ~", "cat /var/run/docker.sock", ":(){ :|:& };:", "mount /dev/sda1 /mnt", "dd if=/dev/zero of=/dev/sda"]:
        assert classify(bad).kind == "block", bad
    assert classify("rm -rf build/").kind == "auto"
    assert classify("").kind == "block"
    assert classify("cat Dockerfile").kind == "auto"


def test_parse_action():
    assert parse_action('```json\n{"command": "ls"}\n```').command == "ls"
    assert parse_action('noise {"done": true, "summary": "x"} noise').done
    assert parse_action("not json") is None
    assert parse_action('{"thought": "no command"}') is None


def test_lenient_final_answer():
    a = parse_action('{"thought": "x", "summary": "Found nothing in /Users"}')
    assert a.done and a.summary == "Found nothing in /Users"
    assert parse_action('{"answer": "42"}').done
    st = TerminalAgent(FakeLLM([cmd("ls"), '{"thought": "t", "answer": "all empty"}']), FakeSession()).run("t")
    assert st.success and st.summary == "all empty"


def test_network_commands_get_longer_timeout():
    s = FakeSession()
    TerminalAgent(FakeLLM([cmd("echo a"), cmd("pip install numpy", True), done]), s, cmd_timeout=30, network_timeout=180).run("t", approve=lambda p: True)
    assert s.timeouts == [30, 180]


def test_repeated_timeouts_stop():
    t = SandboxResult("", "TimeoutExpired", 124, 30, timed_out=True)
    st = TerminalAgent(FakeLLM([cmd("a"), cmd("b"), cmd("c"), "expl"]), FakeSession([t, t, t])).run("t")
    assert not st.success and "timed out" in st.stop_reason


def test_invalid_samples_recorded():
    st = TerminalAgent(FakeLLM(["x1", "x2", "x3", "expl"]), FakeSession()).run("t")
    assert st.invalid_samples == ["x1", "x2", "x3"]


def test_happy_path():
    s = FakeSession()
    agent = TerminalAgent(FakeLLM([cmd("echo hi"), cmd("ls"), done]), s)
    st = agent.run("task")
    assert st.success and st.summary == "all good" and len(st.steps) == 2 and s.ran == [("echo hi", False), ("ls", False)]


def test_network_approval_flow():
    s = FakeSession()
    llm = FakeLLM([cmd("pip install numpy", True), done])
    st = TerminalAgent(llm, s).run("t", approve=lambda p: True)
    assert st.success and s.ran == [("pip install numpy", True)]

    s2, llm2 = FakeSession(), FakeLLM([cmd("pip install numpy", True), done])
    st2 = TerminalAgent(llm2, s2).run("t", approve=lambda p: False)
    assert s2.ran == [] and st2.steps[0].outcome == "denied" and "DENIED" in llm2.calls[1][-1]["content"]
    assert TerminalAgent(FakeLLM([cmd("pip install x"), done]), FakeSession()).run("t").steps[0].outcome == "denied"  # no callback -> deny


def test_blocked_command_never_runs():
    s = FakeSession()
    st = TerminalAgent(FakeLLM([cmd("sudo apt install x"), done]), s).run("t", approve=lambda p: True)
    assert s.ran == [] and st.steps[0].outcome == "blocked"


def test_invalid_json_recovers_then_gives_up():
    st = TerminalAgent(FakeLLM(["oops", cmd("ls"), done]), FakeSession()).run("t")
    assert st.success and st.invalid_replies == 1
    st2 = TerminalAgent(FakeLLM(["a", "b", "c", "explanation"]), FakeSession()).run("t")
    assert not st2.success and "invalid JSON" in st2.stop_reason and st2.explanation == "explanation"


def test_stuck_detection_and_max_steps():
    st = TerminalAgent(FakeLLM([cmd("ls")] * 3 + ["why it failed"]), FakeSession()).run("t")
    assert not st.success and "stuck" in st.stop_reason and st.explanation == "why it failed"
    replies = [cmd(f"echo {i}") for i in range(3)] + ["too many steps"]
    st2 = TerminalAgent(FakeLLM(replies), FakeSession(), max_steps=3).run("t")
    assert not st2.success and "maximum" in st2.stop_reason and len(st2.steps) == 3


def test_three_rejections_stop():
    st = TerminalAgent(FakeLLM([cmd("sudo a"), cmd("sudo b"), cmd("sudo c"), "expl"]), FakeSession()).run("t")
    assert not st.success and "blocked or denied" in st.stop_reason


def test_languages():
    assert "gcc" in shell_command(get_language("c"), "/w/main.c") and "g++" in shell_command(get_language("cpp"), "/w/main.cpp")
    assert shell_command(get_language("python"), "/w/main.py") == "python -u /w/main.py"
    try:
        get_language("cobol")
        assert False
    except ValueError:
        pass


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
    print(f"\nAll {len(tests)} terminal/policy tests passed.")
