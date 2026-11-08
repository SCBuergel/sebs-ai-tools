#!/usr/bin/env python3
"""Deterministic friction scorer over Claude Code session jsonl.

Ranks sessions by how much the agent struggled and emits the exact jsonl line
of each friction moment, so a retrospective reads flagged ranges, not whole
transcripts. No model, no network. Weights live in WEIGHTS below and are the
single source of truth; references/friction-signals.md describes the signals.

Usage:
    scan-sessions.py PATH [PATH ...] [--since ISO] [--until ISO] [--top N] [--json]

PATH is a .jsonl session file or a directory of them (scanned recursively, so
subagents/ transcripts are included). --since/--until filter by file mtime
(YYYY-MM-DD or full ISO). Default output is a table; --json emits the full
structure including per-session moments.
"""
import argparse
import datetime as dt
import glob
import json
import math
import os
import re
import sys

WEIGHTS = {
    "error": 3.0,       # error lexicon in a tool result
    "correction": 4.0,  # correction lexicon in a human turn
    "read_repeat": 2.0, # per re-read of the same file beyond READ_REPEAT_MIN
    "cmd_repeat": 1.5,  # per repeat of an identical bash command beyond CMD_REPEAT_MIN
    "tool_volume": 1.0, # log(total tool calls), a weak size prior
}

# A target reached more than this many times is a navigation anchor; a bash
# command run more than this many times is thrash. Used by both the score and
# the moment anchors, so they stay in step.
READ_REPEAT_MIN = 2
CMD_REPEAT_MIN = 1

# Tools whose repeated use on the same target signals a navigation struggle:
# reaching for the same file, search, or page again and again. Works for any
# session kind, not just coding (research leans on Grep/WebFetch, not Read).
REVISIT_KEYS = {
    "Read": "file_path", "Edit": "file_path", "Write": "file_path",
    "Grep": "pattern", "Glob": "pattern",
    "WebFetch": "url", "WebSearch": "query",
}

ERROR_RE = re.compile(
    r"\b(error|failed|failure|traceback|no such file|not found|"
    r"command not found|permission denied|exit code [1-9]|"
    r"panicked|exception|cannot|unable to)\b",
    re.I,
)
# Tool results of calls that print file or diff content: an error word inside
# the dump is data, not a failed call.
READ_TOOLS = {"Read", "Grep", "Glob", "WebFetch", "WebSearch"}
READ_CMD_RE = re.compile(
    r"^\s*(cat|head|tail|sed\s+-n|grep|rg|diff|ls|find|"
    r"git\s+(--no-pager\s+)?(diff|show|log|status)|just\s+diff|"
    r"gh\s+(pr|issue)\s+(view|diff|list))\b"
)
# Markers that the call itself failed, anchored to a line start so a dump that
# only contains the word does not match.
STRICT_ERROR_RE = re.compile(
    r"^(exit code [1-9]\d*|error[:\[ ]|fatal:|traceback \(most recent|"
    r"<tool_use_error>|[^\n]*hook error:|[^\n]*: command not found|"
    r"[^\n]*: no such file or directory|failed to |cannot )",
    re.I | re.M,
)
SHORT_RESULT = 600  # the loose error lexicon counts only in a short result
CORRECTION_RE = re.compile(
    # "no," / "no." stand alone: a trailing \b would never follow the punctuation.
    r"\bno[,.]|"
    r"\b(not what|that'?s wrong|that'?s not|actually|revert|undo|"
    r"roll ?back|backtrack|back out|stop|don'?t|instead|"
    r"you misunderstood|try again|not quite|incorrect)\b",
    re.I,
)
# Markers of harness-injected user entries (system reminders, slash-command
# wrappers). Their text carries the correction lexicon without a human saying
# it, which would bias the ranking, so they are skipped on the correction path.
INJECTED_RE = re.compile(
    r"<(system-reminder|command-name|command-message|command-args|"
    r"local-command-stdout|task-notification|agent-message)>"
)
MOMENT_CAP = 20  # most flagged moments reported per session


def text_of(content):
    """Flatten a message.content (str or list of blocks) to searchable text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if not isinstance(b, dict):
                continue
            if isinstance(b.get("text"), str):
                out.append(b["text"])
            c = b.get("content")
            if isinstance(c, str):
                out.append(c)
            elif isinstance(c, list):
                out.append(text_of(c))
        return " ".join(out)
    return ""


def is_tool_result(content):
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


def is_error_result(text, tool_name, command):
    """True when a tool result shows the call itself failed."""
    if STRICT_ERROR_RE.search(text):
        return True
    if tool_name in READ_TOOLS:
        return False
    if tool_name == "Bash" and READ_CMD_RE.search(command or ""):
        return False
    return len(text) <= SHORT_RESULT and bool(ERROR_RE.search(text))


def is_injected(entry, raw_line):
    """True for a harness-injected user entry, not a genuine human turn."""
    return bool(entry.get("isMeta")) or INJECTED_RE.search(raw_line) is not None


def revisit_target(name, inp):
    """The thing a navigation tool reached for, if this tool is one (else None)."""
    key = REVISIT_KEYS.get(name)
    v = inp.get(key) if key else None
    return v.strip() if isinstance(v, str) and v.strip() else None


def excerpt(s, n=160):
    # Bound before normalizing so a multi-MB line costs a constant, not its size.
    return re.sub(r"\s+", " ", s[: n + 240]).strip()[:n]


def score_session(path):
    reads = {}       # revisit target (file / search / page) -> [count, first_line]
    cmds = {}        # command -> [count, first_line]
    tool_total = 0
    tools = {}       # tool_use id -> (tool name, bash command)
    err_hits = []    # (line, excerpt)
    corr_hits = []   # (line, excerpt)
    seen_assistant = False  # a correction is a human turn that follows agent action

    def bump(d, key, ln):
        e = d.get(key)
        if e:
            e[0] += 1
        else:
            d[key] = [1, ln]

    with open(path, errors="replace") as fh:
        for ln, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            typ = o.get("type")
            msg = o.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None

            if typ == "assistant":
                seen_assistant = True
                if isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict) or b.get("type") != "tool_use":
                            continue
                        tool_total += 1
                        name = b.get("name")
                        inp = b.get("input", {}) or {}
                        tools[b.get("id")] = (name, inp.get("command"))
                        target = revisit_target(name, inp)
                        if target:
                            bump(reads, target, ln)
                        if name == "Bash" and inp.get("command"):
                            bump(cmds, inp["command"].strip(), ln)

            elif typ == "user":
                if is_tool_result(content):
                    t = text_of(content)
                    use_id = next((b.get("tool_use_id") for b in content
                                   if isinstance(b, dict) and b.get("type") == "tool_result"), None)
                    name, command = tools.get(use_id, (None, None))
                    if is_error_result(t, name, command):
                        err_hits.append((ln, excerpt(t)))
                elif seen_assistant and not is_injected(o, raw):
                    t = text_of(content)
                    if t and CORRECTION_RE.search(t):
                        corr_hits.append((ln, excerpt(t)))

    read_repeat = sum(max(0, c - READ_REPEAT_MIN) for c, _ in reads.values())
    cmd_repeat = sum(max(0, c - CMD_REPEAT_MIN) for c, _ in cmds.values())
    signals = {
        "errors": len(err_hits),
        "corrections": len(corr_hits),
        "read_repeats": read_repeat,
        "cmd_repeats": cmd_repeat,
        "tool_calls": tool_total,
    }
    score = (
        WEIGHTS["error"] * len(err_hits)
        + WEIGHTS["correction"] * len(corr_hits)
        + WEIGHTS["read_repeat"] * read_repeat
        + WEIGHTS["cmd_repeat"] * cmd_repeat
        + WEIGHTS["tool_volume"] * math.log1p(tool_total)
    )

    hits = (
        [{"line": l, "kind": "error", "excerpt": e} for l, e in err_hits]
        + [{"line": l, "kind": "correction", "excerpt": e} for l, e in corr_hits]
    )
    # busiest revisit / repeated command, each anchored to its first occurrence.
    # These drive read_repeats/cmd_repeats in the score, so they always survive
    # the cap; only the error/correction hits are trimmed to fit.
    anchors = []
    for fp, (c, fl) in sorted(reads.items(), key=lambda kv: -kv[1][0])[:3]:
        if c > READ_REPEAT_MIN:
            anchors.append({"line": fl, "kind": "read_repeat",
                            "excerpt": f"{c}x {fp}"})
    for cmd, (c, fl) in sorted(cmds.items(), key=lambda kv: -kv[1][0])[:3]:
        if c > CMD_REPEAT_MIN:
            anchors.append({"line": fl, "kind": "cmd_repeat",
                            "excerpt": f"{c}x {excerpt(cmd, 80)}"})
    hits.sort(key=lambda m: m["line"])
    moments = hits[: max(0, MOMENT_CAP - len(anchors))] + anchors
    moments.sort(key=lambda m: m["line"])
    return {
        "session": path,
        "score": round(score, 2),
        "signals": signals,
        "moments": moments,
    }


def collect(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += glob.glob(os.path.join(p, "**", "*.jsonl"), recursive=True)
        elif p.endswith(".jsonl"):
            files.append(p)
    return sorted(set(files))


def parse_date(s, end=False):
    # fromisoformat parses "YYYY-MM-DD" and full ISO; normalize a trailing Z,
    # which fromisoformat rejects before Python 3.11.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    t = dt.datetime.fromisoformat(s)
    # A date-only upper bound means "through the end of that day", so a session
    # modified any time on the named day stays in the window.
    if end and len(s) == 10:
        t += dt.timedelta(days=1) - dt.timedelta(seconds=1)
    return t.timestamp()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    since = parse_date(a.since) if a.since else None
    until = parse_date(a.until, end=True) if a.until else None
    files = collect(a.paths)
    if since is not None or until is not None:
        kept = []
        for f in files:
            m = os.path.getmtime(f)
            if (since is None or m >= since) and (until is None or m <= until):
                kept.append(f)
        files = kept
    if not files:
        print("no session files matched", file=sys.stderr)
        return 1

    scored = []
    for f in files:
        try:
            scored.append(score_session(f))
        except OSError as e:
            print(f"skipped {f}: {e}", file=sys.stderr)
    ranked = sorted(scored, key=lambda r: -r["score"])[:a.top]

    if a.json:
        json.dump(ranked, sys.stdout, indent=2)
        print()
        return 0

    print(f"{'score':>7}  {'err':>3} {'corr':>4} {'rrd':>3} {'cmd':>3} "
          f"{'tool':>4}  session")
    for r in ranked:
        s = r["signals"]
        print(f"{r['score']:>7.1f}  {s['errors']:>3} {s['corrections']:>4} "
              f"{s['read_repeats']:>3} {s['cmd_repeats']:>3} {s['tool_calls']:>4}  "
              f"{os.path.basename(r['session'])}")
    print("\nrun with --json for per-session moment lines", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
