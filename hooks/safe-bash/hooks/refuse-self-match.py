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

QUOTED_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'')
COMMENT_RE = re.compile(r"(?:^|(?<=\s))#[^\n]*", re.M)
# <<EOF, <<-'EOF', <<"EOF": the redirection; group 2 is the delimiter.
HEREDOC_RE = re.compile(r"<<(-?)\s*['\"]?(\w+)['\"]?")
DELIM_LINE = r"^\t*{}[ \t]*$"
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
    """What the shell runs as commands: quoted literals, comments and heredoc bodies dropped,
    command substitutions kept, also inside double quotes and however their own quotes nest.
    A quote that never closes is not a quote: the text after it stays visible, so a stray
    apostrophe cannot hide a later command. Returns (text, next index)."""
    out = []
    while i < len(text):
        c = text[i]
        if stop and c == stop:
            return "".join(out), i + 1
        word_start = i == 0 or text[i - 1] in " \t\n;&|()"
        if c == "\\":
            out.append(" ")
            i += 2
        elif c == "#" and word_start:
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
        elif text.startswith("<<", i) and not text.startswith("<<<", i):
            m = HEREDOC_RE.match(text, i)
            if not m:
                out.append(c)
                i += 1
                continue
            out.append(text[i:m.end()])
            # the body runs from the next line to a line holding only the delimiter
            line_end = text.find("\n", m.end())
            body = DELIM_LINE.format(re.escape(m.group(2)))
            close = re.compile(body, re.M).search(text, line_end + 1) if line_end >= 0 else None
            i = len(text) if line_end < 0 else (close.end() if close else len(text))
            if line_end >= 0:
                out.append(visible(text[m.end():line_end])[0] + "\n")
        elif c == "'":
            end = text.find("'", i + 1)
            if end < 0:
                out.append(" ")
                i += 1
            else:
                out.append('""')
                i = end + 1
        elif c == '"':
            j, kept = i + 1, []
            while j < len(text) and text[j] != '"':
                if text[j] == "\\":
                    j += 2
                elif text.startswith("$(", j):
                    inner, j = visible(text, j + 2, ")")
                    kept.append(f"$({inner})")
                elif text[j] == "`":
                    inner, j = visible(text, j + 1, "`")
                    kept.append(f"`{inner}`")
                else:
                    j += 1
            if j >= len(text):  # never closed
                out.append(" ")
                i += 1
            else:
                out.append('"' + "".join(kept) + '"')
                i = j + 1
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
    """The commands in a Bash call, read two ways: by the scanner above, and with only
    complete quoted strings blanked. A refusal needs either to see the call, so a case one
    of them misreads is still caught by the other. Plus each quoted `sh -c` script."""
    yield visible(command)[0]
    yield QUOTED_RE.sub('""', COMMENT_RE.sub("", command))
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
