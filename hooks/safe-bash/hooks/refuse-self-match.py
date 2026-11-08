#!/usr/bin/env python3
"""PreToolUse hook on Bash: refuse `pkill -f` and `pgrep -f` (or `--full`).

They match against each process's full command line, and the shell that runs the command has
the pattern in its own command line, so it matches itself: pkill kills that shell (exit 144,
the rest of the output lost) and pgrep reports it. Exit 2 refuses the call and shows the reason
to the agent; any other input passes.
"""
import json
import re
import sys

# A quoted script run by a shell is a command too.
SHELL_C_RE = re.compile(r'\b(?:ba|z|da)?sh\s+-\w*c\w*\s+("(?:[^"\\]|\\.)*"|\'[^\']*\')')
# pkill or pgrep in command position: at a line start, after a separator or a shell keyword,
# after VAR=value assignments, or after a wrapper command (sudo -u me, timeout -s KILL 5, ...)
# with whatever options it takes.
CALL_RE = re.compile(
    r"(?:^|[;&|(){}!`\n]|\$\(|\b(?:then|do|else|elif|time)\b)\s*"
    r"(?:\w+=\S*\s+)*"
    r"(?:(?:sudo|env|nice|ionice|nohup|exec|command|xargs|timeout|stdbuf|setsid|chrt)\b[^;&|\n]*?\s)?"
    r"(?:\S*/)?(pkill|pgrep)\b([^;&|\n)`}]*)",
    re.M,
)
FULL_FLAG = re.compile(r"-[A-Za-z0-9]*f[A-Za-z0-9]*|--full")
REASON = ("Refused: `{call}` matches the full command line of every process, including the shell "
          "running this command, so it kills or finds itself. Kill by PID instead: record the PID "
          "when you start the process (`cmd & echo $! > /tmp/x.pid`, then `kill $(cat /tmp/x.pid)`), "
          "or find it with `pgrep -x <name>` or `ps -eo pid,args | grep '[p]attern'` and kill that PID.")


def visible(text, i=0, stop=None):
    """What the shell runs as commands: quoted literals dropped, command substitutions kept,
    also inside double quotes and however their own quotes nest. Returns (text, next index)."""
    out = []
    while i < len(text):
        c = text[i]
        if stop and c == stop:
            return "".join(out), i + 1
        if c == "\\":
            out.append(" ")
            i += 2
        elif c == "'":
            end = text.find("'", i + 1)
            i = len(text) if end < 0 else end + 1
            out.append('""')
        elif c == '"':
            i += 1
            out.append('"')
            while i < len(text) and text[i] != '"':
                if text[i] == "\\":
                    i += 2
                elif text.startswith("$(", i):
                    inner, i = visible(text, i + 2, ")")
                    out.append(f"$({inner})")
                elif text[i] == "`":
                    inner, i = visible(text, i + 1, "`")
                    out.append(f"`{inner}`")
                else:
                    i += 1
            out.append('"')
            i += 1
        elif text.startswith("$(", i) or c == "(":
            inner, i = visible(text, i + (2 if c == "$" else 1), ")")
            out.append(f"{'$' if c == '$' else ''}({inner})")
        elif c == "`" and stop != "`":
            inner, i = visible(text, i + 1, "`")
            out.append(f"`{inner}`")
        else:
            out.append(c)
            i += 1
    return "".join(out), i


def commands(command):
    """The commands in a Bash call, plus each quoted `sh -c` script."""
    yield visible(command)[0]
    for m in SHELL_C_RE.finditer(command):
        yield from commands(m.group(1)[1:-1])


def refusal(command):
    for text in commands(command or ""):
        for m in CALL_RE.finditer(text):
            program, args = m.group(1), m.group(2).split()
            if any(FULL_FLAG.fullmatch(a) for a in args):
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
