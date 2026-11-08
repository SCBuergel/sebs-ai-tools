#!/usr/bin/env python3
"""PreToolUse hook on Bash: refuse `pkill -f`, `pgrep -f` (or `--full`) and `killall -r`.

They match against each process's full command line, and the shell that runs the command has
the pattern in its own command line, so it matches itself: pkill kills that shell (exit 144,
the rest of the output lost) and pgrep reports it. Exit 2 refuses the call and shows the reason
to the agent; any other input passes.
"""
import json
import re
import sys

# A program in command position: at a line start, after a separator, or after a prefix
# command such as sudo or timeout.
CALL_RE = re.compile(
    r"(?:^|[;&|(`]|\$\()\s*(?:(?:sudo|exec|command|nohup|xargs|env|timeout\s+\S+)\s+)*"
    r"(?:\S*/)?(pkill|pgrep|killall)\b([^;&|\n)`]*)",
    re.M,
)
FULL_FLAG = {"pkill": re.compile(r"-[A-Za-z0-9]*f[A-Za-z0-9]*|--full"),
             "pgrep": re.compile(r"-[A-Za-z0-9]*f[A-Za-z0-9]*|--full"),
             "killall": re.compile(r"-[A-Za-z]*r[A-Za-z]*|--regexp")}
REASON = ("Refused: `{call}` matches the full command line of every process, including the shell "
          "running this command, so it kills or finds itself. Kill by PID instead: record the PID "
          "when you start the process (`cmd & echo $! > /tmp/x.pid`, then `kill $(cat /tmp/x.pid)`), "
          "or find it with `pgrep -x <name>` or `ps -eo pid,args | grep '[p]attern'` and kill that PID.")


def refusal(command):
    for m in CALL_RE.finditer(command or ""):
        program, args = m.group(1), m.group(2).split()
        if any(FULL_FLAG[program].fullmatch(a) for a in args):
            return REASON.format(call=f"{program} {' '.join(args)}".strip())
    return None


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool_input = event.get("tool_input") if isinstance(event, dict) else None
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    why = refusal(command) if isinstance(command, str) else None
    if why:
        print(why, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
