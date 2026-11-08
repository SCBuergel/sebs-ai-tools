#!/usr/bin/env python3
"""Deterministic friction scorer over Claude Code session jsonl.

Ranks runs by how much the agents struggled and emits the exact jsonl line
of each friction moment, so a retrospective reads flagged ranges, not whole
transcripts. No model, no network. Weights live in WEIGHTS below and are the
single source of truth; references/friction-signals.md describes the signals.

A run is one session together with the subagents it started: a main transcript
<uuid>.jsonl and every <uuid>/subagents/*.jsonl beside it. --run treats all
given transcripts as one run, for a session continued under a new id.

Usage:
    scan-sessions.py PATH [PATH ...] [--since ISO] [--until ISO] [--top N]
                     [--run] [--coordination LOG ...] [--coordination-kinds RE]
                     [--moments N] [--json]

PATH is a .jsonl session file or a directory of them (scanned recursively, so
subagents/ transcripts are included). --since/--until filter by file mtime
(YYYY-MM-DD or full ISO). --coordination adds an orchestrator's event log
(JSONL, one event per line) to a single run. Default output is a table; --json
emits the full structure including per-transcript moments.
"""
import argparse
import datetime as dt
import glob
import json
import math
import os
import re
import statistics
import sys

WEIGHTS = {
    "error": 3.0,       # error lexicon in a tool result
    "correction": 4.0,  # correction lexicon in a human turn
    "read_repeat": 2.0, # per re-read of the same file beyond READ_REPEAT_MIN
    "cmd_repeat": 1.5,  # per repeat of an identical bash command beyond CMD_REPEAT_MIN
    "tool_volume": 1.0, # log(total tool calls), a weak size prior
    "steer": 1.0,       # a human turn without the correction lexicon: plain redirection
    "rework": 1.5,      # a parent's follow-up message to a subagent: another round
    "agent_failure": 2.0,  # a subagent the harness reports as not completed
    "coordination": 2.0,   # a negative event in an orchestrator's log
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
    r"local-command-stdout|task-notification|agent-message|"
    r"cross-session-message|teammate-message)(\s[^>]*)?>"
)
PEER_RE = re.compile(r"<(cross-session-message|teammate-message)\b")
# The harness reports each subagent's end, with its usage, in the parent's transcript.
NOTIFICATION_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
MOMENT_CAP = 20  # flagged moments reported per transcript of up to 1500 lines
MOMENT_CAP_MAX = 120  # a longer transcript gets 20 more per 1500 lines, up to this
COORDINATION_CAP = 60  # coordination moments reported per run
COORDINATION_KINDS = (
    r"stop|stall|quiet|invalid|duplicate|unresolved|revise|reject|block|dispute|"
    r"timeout|fail|lost|conflict|retry|abandon|error|expired|refus"
)
PRIORITY_KINDS = ("correction", "steer", "rework", "agent_failure", "peer")


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


def moment_cap(lines, override=None):
    """More room in longer transcripts, so a long session is not judged by its start."""
    if override:
        return override
    return min(MOMENT_CAP_MAX, MOMENT_CAP * max(1, math.ceil(lines / 1500)))


def spread(items, k):
    """k items picked evenly across items in line order: the start, middle and end all show."""
    if k <= 0:
        return []
    if len(items) <= k:
        return list(items)
    if k == 1:
        return [items[0]]
    return [items[round(i * (len(items) - 1) / (k - 1))] for i in range(k)]


def notification(text):
    """id, status and usage of one task notification, from the harness's tags."""
    def tag(name):
        m = re.search(rf"<{name}>\s*([^<]*?)\s*</{name}>", text)
        return m.group(1) if m else None

    def number(*names):
        for name in names:
            value = tag(name)
            if value and value.isdigit():
                return int(value)
        return None

    return {
        "id": tag("task-id"),
        "status": tag("status"),
        "tokens": number("subagent_tokens", "total_tokens"),
        "tool_uses": number("tool_uses"),
        "duration_ms": number("duration_ms"),
    }


def score_session(path, moments_override=None):
    reads = {}       # revisit target (file / search / page) -> [count, first_line]
    cmds = {}        # command -> [count, first_line]
    tool_total = 0
    tools = {}       # tool_use id -> (tool name, bash command)
    err_hits = []    # (line, excerpt)
    corr_hits = []   # (line, excerpt)
    steer_hits = []  # (line, excerpt): human turns without the correction lexicon
    rework_hits = [] # (line, excerpt): a parent's follow-up to this subagent
    peer_hits = []   # (line, excerpt): messages from other sessions
    usage = {}       # subagent id -> latest notification, with its line
    sidechain = "/subagents/" in path.replace(os.sep, "/")
    seen_assistant = False  # a correction is a human turn that follows agent action
    lines = 0

    def bump(d, key, ln):
        e = d.get(key)
        if e:
            e[0] += 1
        else:
            d[key] = [1, ln]

    with open(path, errors="replace") as fh:
        for ln, raw in enumerate(fh, 1):
            lines = ln
            raw = raw.strip()
            if not raw:
                continue
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(o, dict):
                continue
            sidechain = sidechain or bool(o.get("isSidechain"))
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
                elif is_injected(o, raw):
                    t = text_of(content)
                    for block in NOTIFICATION_RE.findall(t):
                        n = notification(block)
                        if n["id"]:
                            previous = usage.get(n["id"], {})
                            # Each notification repeats the totals so far: keep the latest.
                            usage[n["id"]] = {
                                **n,
                                "tokens": max(n["tokens"] or 0, previous.get("tokens") or 0),
                                "line": ln,
                            }
                    if PEER_RE.search(t):
                        peer_hits.append((ln, excerpt(t)))
                elif seen_assistant:
                    t = text_of(content)
                    if not t:
                        continue
                    if sidechain:
                        # In a subagent's transcript the "user" is the agent that started it.
                        rework_hits.append((ln, excerpt(t)))
                    elif CORRECTION_RE.search(t):
                        corr_hits.append((ln, excerpt(t)))
                    else:
                        steer_hits.append((ln, excerpt(t)))

    read_repeat = sum(max(0, c - READ_REPEAT_MIN) for c, _ in reads.values())
    cmd_repeat = sum(max(0, c - CMD_REPEAT_MIN) for c, _ in cmds.values())
    failures = [n for n in usage.values() if n["status"] and n["status"] != "completed"]
    signals = {
        "errors": len(err_hits),
        "corrections": len(corr_hits),
        "steers": len(steer_hits),
        "reworks": len(rework_hits),
        "peer_messages": len(peer_hits),
        "agent_failures": len(failures),
        "read_repeats": read_repeat,
        "cmd_repeats": cmd_repeat,
        "tool_calls": tool_total,
        "lines": lines,
    }
    score = (
        WEIGHTS["error"] * len(err_hits)
        + WEIGHTS["correction"] * len(corr_hits)
        + WEIGHTS["steer"] * len(steer_hits)
        + WEIGHTS["rework"] * len(rework_hits)
        + WEIGHTS["agent_failure"] * len(failures)
        + WEIGHTS["read_repeat"] * read_repeat
        + WEIGHTS["cmd_repeat"] * cmd_repeat
        + WEIGHTS["tool_volume"] * math.log1p(tool_total)
    )

    def as_moments(found, kind):
        return [{"line": line, "kind": kind, "excerpt": text} for line, text in found]

    priority = (
        as_moments(corr_hits, "correction")
        + as_moments(steer_hits, "steer")
        + as_moments(rework_hits, "rework")
        + as_moments(peer_hits, "peer")
        + [
            {"line": n["line"], "kind": "agent_failure",
             "excerpt": f"subagent {n['id']} ended {n['status']}"}
            for n in failures
        ]
    )
    errors = as_moments(err_hits, "error")
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
    # Human and coordination moments first, since they are few and each is a real turn;
    # errors fill what is left. Both are spread across the whole transcript, so a long
    # session is not judged by its start.
    room = max(0, moment_cap(lines, moments_override) - len(anchors))
    priority.sort(key=lambda m: m["line"])
    errors.sort(key=lambda m: m["line"])
    chosen = spread(priority, room)
    chosen += spread(errors, room - len(chosen))
    moments = sorted(chosen + anchors, key=lambda m: m["line"])
    return {
        "session": path,
        "score": round(score, 2),
        "signals": signals,
        "moments": moments,
        "usage": usage,
        "sidechain": sidechain,
    }


# --- runs: a session with its subagents ---------------------------------------


def run_key(path):
    """A subagent's transcript belongs to the run of the session folder it sits in."""
    parts = os.path.normpath(path).split(os.sep)
    if "subagents" in parts:
        return parts[parts.index("subagents") - 1]
    return os.path.splitext(os.path.basename(path))[0]


def agent_meta(path):
    """What the harness recorded about a subagent beside its transcript, if anything."""
    meta = os.path.splitext(path)[0] + ".meta.json"
    try:
        with open(meta) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    name = os.path.splitext(os.path.basename(path))[0]
    return {
        "id": name.removeprefix("agent-"),
        "type": data.get("agentType"),
        "description": data.get("description"),
        "finished": data.get("finished"),
    }


def read_coordination(paths, kinds):
    """Negative events from orchestrator logs, as moments, plus counts of every kind."""
    pattern = re.compile(kinds, re.I)
    counts, by_agent, moments = {}, {}, []
    for path in paths:
        with open(path, errors="replace") as fh:
            for ln, raw in enumerate(fh, 1):
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                kind = next(
                    (str(event[k]) for k in ("kind", "type", "event", "status") if event.get(k)),
                    None,
                )
                if kind is None:
                    continue
                counts[kind] = counts.get(kind, 0) + 1
                if not pattern.search(kind):
                    continue
                agent = next(
                    (str(event[k]) for k in ("agent", "actor", "role") if event.get(k)), "?"
                )
                by_agent[agent] = by_agent.get(agent, 0) + 1
                detail = " ".join(
                    str(event[k]) for k in ("id", "msg", "message") if event.get(k)
                )
                moments.append({"file": path, "line": ln, "kind": "coordination",
                                "excerpt": excerpt(f"{agent} {kind} {detail}")})
    return counts, by_agent, moments


def score_run(key, files, coordination=(), kinds=COORDINATION_KINDS, moments_override=None):
    members = []
    for f in sorted(files):
        try:
            member = score_session(f, moments_override)
        except OSError as e:
            print(f"skipped {f}: {e}", file=sys.stderr)
            continue
        member["agent"] = agent_meta(f)
        members.append(member)

    # Usage comes from notifications in the parent; join it to each subagent by id.
    usage = {}
    for member in members:
        for agent_id, n in member["usage"].items():
            usage[agent_id] = {**n, "file": member["session"]}
    for member in members:
        agent = member["agent"]
        member["cost"] = usage.get(agent["id"]) if agent else None

    moments = []
    for member in members:
        agent = member["agent"]
        if agent and agent["finished"] is False:
            moments.append({"file": member["session"], "line": 1, "kind": "unfinished",
                            "excerpt": f"{agent['type']}: {agent['description']}"})
    spent = [n for n in usage.values() if n.get("tokens")]
    if len(spent) >= 3:
        middle = statistics.median(n["tokens"] for n in spent)
        heavy = sorted(spent, key=lambda n: -n["tokens"])[:3]
        for n in heavy:
            if n["tokens"] > 2 * middle:
                moments.append({"file": n["file"], "line": n["line"], "kind": "cost",
                                "excerpt": f"subagent {n['id']}: {n['tokens']} tokens, "
                                           f"{n.get('tool_uses')} tool calls, "
                                           f"{n.get('duration_ms')} ms (median {int(middle)})"})

    counts, by_agent, events = {}, {}, []
    if coordination:
        counts, by_agent, events = read_coordination(coordination, kinds)
    moments += spread(events, COORDINATION_CAP)

    signals = {}
    for member in members:
        for name, value in member["signals"].items():
            signals[name] = signals.get(name, 0) + value
    signals["subagents"] = sum(1 for m in members if m["sidechain"])
    signals["coordination"] = len(events)
    signals["tokens"] = sum(n.get("tokens") or 0 for n in usage.values())
    score = sum(m["score"] for m in members) + WEIGHTS["coordination"] * len(events)
    members.sort(key=lambda m: -m["score"])
    for member in members:
        del member["usage"]
    return {
        "run": key,
        "score": round(score, 2),
        "signals": signals,
        "moments": sorted(moments, key=lambda m: (m["file"], m["line"])),
        "coordination_kinds": counts,
        "coordination_by_agent": by_agent,
        "members": members,
    }


def group_runs(files, one_run=False):
    runs = {}
    for f in files:
        runs.setdefault("run" if one_run else run_key(f), []).append(f)
    return runs


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
    ap.add_argument("--run", action="store_true",
                    help="treat every given transcript as one run, e.g. a continued session")
    ap.add_argument("--coordination", action="append", default=[],
                    help="an orchestrator's JSONL event log, for a single run")
    ap.add_argument("--coordination-kinds", default=COORDINATION_KINDS,
                    help="regex of event kinds that count as friction")
    ap.add_argument("--moments", type=int, help="moments per transcript, instead of by length")
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

    runs = group_runs(files, a.run)
    if a.coordination and len(runs) != 1:
        print("--coordination needs exactly one run: name one session, or add --run",
              file=sys.stderr)
        return 2
    scored = [
        score_run(key, members, a.coordination, a.coordination_kinds, a.moments)
        for key, members in runs.items()
    ]
    ranked = sorted(scored, key=lambda r: -r["score"])[:a.top]

    if a.json:
        json.dump(ranked, sys.stdout, indent=2)
        print()
        return 0

    header = (f"{'score':>8}  {'err':>4} {'corr':>4} {'steer':>5} {'rwk':>4} "
              f"{'fail':>4} {'coord':>5} {'tool':>5}  run / transcript")
    print(header)
    for r in ranked:
        s = r["signals"]
        print(f"{r['score']:>8.1f}  {s['errors']:>4} {s['corrections']:>4} {s['steers']:>5} "
              f"{s['reworks']:>4} {s['agent_failures']:>4} {s['coordination']:>5} "
              f"{s['tool_calls']:>5}  {r['run']} ({s['subagents']} subagents, "
              f"{s['tokens']} subagent tokens)")
        for m in r["members"][:10]:
            ms, agent = m["signals"], m["agent"]
            who = f"{agent['type']}: {agent['description']}" if agent else "main"
            print(f"{m['score']:>8.1f}  {ms['errors']:>4} {ms['corrections']:>4} "
                  f"{ms['steers']:>5} {ms['reworks']:>4} {ms['agent_failures']:>4} "
                  f"{'':>5} {ms['tool_calls']:>5}    {os.path.basename(m['session'])} "
                  f"{excerpt(who, 60)}")
    print("\nrun with --json for per-transcript moment lines", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
