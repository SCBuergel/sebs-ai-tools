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
    "ls\npkill -f x",
    "if true; then pkill -f x; fi",
    "if pgrep -f x; then :; fi",
    "while pgrep -f x; do sleep 1; done",
    "until ! pgrep -f x; do sleep 1; done",
    "eval pkill -f x",
    "eval 'pkill -f x'",
    "watch pgrep -f x",
    "doas pkill -f x",
    "flock /tmp/l pkill -f x",
    "systemd-run --user pkill -f x",
    "busybox pkill -f x",
    "coproc pkill -f x",
    "while sleep 1; do pgrep -f x; done",
    "{ pkill -f x; }",
    "! pgrep -f x",
    'bash -c "pkill -f x"',
    "sh -c 'sleep 1; pkill -f x'",
    "env FOO=1 pkill -f x",
    "FOO=1 pkill -f x",
    "sudo -u me pkill -f x",
    "nice pkill -f x",
    "timeout -s KILL 5 pkill -f x",
    "ls | xargs -r pkill -f",
    'kill "$(pgrep -f x)"',
    'echo "$(pkill -f x)"',
    'p="`pgrep -f x`"',
    'kill "$(pgrep -f "board mergeq")"',
    'echo "pid: $(pgrep -f "dash.py")"',
    'kill $(pgrep -fa "18765|board")',
    'echo "$( (cd /tmp; pkill -f x) )"',
    "# stop the server, it's stuck\npkill -f server",
    "ls  # don't care\npgrep -f x",
    "cat <<EOF > f\nit's\nEOF\npkill -f x",
    "cat <<'EOF' > f\nit's 'quoted'\nEOF\npkill -f x",
    "echo it's\npkill -f x",
    'echo "unclosed\npkill -f x',
    # a heredoc body may be a script (bash <<EOF): a command line in it is refused too
    "bash <<EOF\npkill -f x\nEOF",
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
    "killall -r 'dash.*'",  # -r matches process names, not command lines
    "kill $(cat /tmp/x.pid)",
    "ps -eo pid,args | grep '[b]oard mergeq'",
    'git commit -m "safe-bash: refuse pkill -f"',
    "grep -rn 'pkill -f' docs/",
    "echo pkill -f is refused",
    'git commit -m "a; pkill -f b"',
    "echo 'then pkill -f x'",
    "echo '$(pkill -f x)'",  # single quotes: no substitution runs
    'echo "a \\"; pkill -f b"',  # an escaped quote stays inside the string
    "printf '%s' \"it's\" && pgrep -x x",
    "# never use pkill -f here\nls",
    "git log --grep='#1' && ls",
])
def test_lets_everything_else_through(command):
    r = run(command)
    assert (r.returncode, r.stderr) == (0, "")


def test_passes_input_it_cannot_read():
    for raw in ("", "not json", "[]", '{"tool_input": "x"}', '{"tool_input": {"command": 5}}'):
        r = subprocess.run([sys.executable, str(HOOK)], input=raw, capture_output=True, text=True)
        assert r.returncode == 0
