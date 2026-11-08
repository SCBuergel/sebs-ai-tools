"""Tests for the safe-bash hook. Run: python3 -m pytest hooks/safe-bash/tests"""
import json
import pathlib
import subprocess
import sys

import pytest

HOOK = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "refuse-self-match.py"


def run(command, tool="Bash"):
    event = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"command": command}}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event),
                          capture_output=True, text=True)


@pytest.mark.parametrize("command", [
    'pkill -f "board mergeq"',
    "pkill -9f dashweb.py",
    "pkill --full 'python3 dash.py'",
    "pkill -u user -f worker",
    "pgrep -f mergeq",
    "pgrep -af 'python3 .*board'",
    "cd /tmp && pkill -f x",
    "sleep 1; pkill -f x",
    "sudo pkill -f x",
    "timeout 5 pgrep -f x",
    "/usr/bin/pkill -f x",
    "echo $(pgrep -f x)",
    "killall -r 'dash.*'",
    "killall --regexp dash",
    "ls\npkill -f x",
])
def test_refuses_a_full_command_line_match(command):
    r = run(command)
    assert r.returncode == 2
    assert "Kill by PID" in r.stderr


@pytest.mark.parametrize("command", [
    "pkill dashweb",
    "pkill -x python3",
    "pkill -F /tmp/x.pid",
    "pgrep -x mergeq",
    "pgrep -l board",
    "killall dash",
    "kill $(cat /tmp/x.pid)",
    "ps -eo pid,args | grep '[b]oard mergeq'",
    'git commit -m "safe-bash: refuse pkill -f"',
    "grep -rn 'pkill -f' docs/",
    "echo pkill -f is refused",
])
def test_lets_everything_else_through(command):
    r = run(command)
    assert (r.returncode, r.stderr) == (0, "")


def test_passes_input_it_cannot_read():
    for raw in ("", "not json", "[]", '{"tool_input": "x"}', '{"tool_input": {"command": 5}}'):
        r = subprocess.run([sys.executable, str(HOOK)], input=raw, capture_output=True, text=True)
        assert r.returncode == 0
