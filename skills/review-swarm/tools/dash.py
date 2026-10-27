#!/usr/bin/env python3
"""Live terminal view of a review swarm.  python3 dash.py  (Ctrl-C quits)

Reads swarm.json (title, reviewer and dev counts), events.jsonl, queue/, val/ and slots/ next to this file.
Standard library only. When this file changes on disk it self-tests the new version and re-executes itself.
"""
import glob, json, os, re, shutil, subprocess, sys, textwrap, time, traceback

SRC = os.path.realpath(__file__)
D = os.path.dirname(SRC)
EV = os.path.join(D, "events.jsonl")
R, B, DIM, IT = "\033[0m", "\033[1m", "\033[2m", "\033[3m"


def fg(n):
    return f"\033[38;5;{n}m"


def bgc(n):
    return f"\033[48;5;{n}m"


SEV = {"Critical": 196, "High": 202, "Medium": 214, "Low": 45, "Info": 245}

def readj(p):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


CFG_PATH = os.path.join(D, "swarm.json")
CFG = readj(CFG_PATH)
TITLE = str(CFG.get("title") or "REVIEW SWARM")
# Configured roster; agents seen in the log are added by roster(), so the view also works without swarm.json.
DEVS = [f"dev{i}" for i in range(1, int(CFG.get("devs") or 0) + 1)]
REVIEWERS = [f"R{i}" for i in range(1, int(CFG.get("reviewers") or 0) + 1)]
AGENT_COL = {"coordinator": 75, "dispatcher": 177, "dash": 141}
DEV_COL = [220, 214, 184, 178, 172, 186, 208, 222, 229, 215]
REV_COL = [81, 117, 153, 189, 225, 219, 213, 207, 171, 135]
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
PULSE = "▁▂▃▄▅▆▇█▇▆▅▄▃▂"


def agent_col(a):
    if a in AGENT_COL:
        return AGENT_COL[a]
    if a.startswith("dev") and a[3:].isdigit():
        return DEV_COL[(int(a[3:]) - 1) % len(DEV_COL)]
    if a.startswith("R") and a[1:].isdigit():
        return REV_COL[(int(a[1:]) - 1) % len(REV_COL)]
    return 250


def roster(evs):
    """Add reviewers (R<n>) and devs (dev<n>) that appear in the log to the configured lists."""
    for a in {e.get("agent", "") for e in evs}:
        if a.startswith("R") and a[1:].isdigit() and a not in REVIEWERS:
            REVIEWERS.append(a)
        if a.startswith("dev") and a[3:].isdigit() and a not in DEVS:
            DEVS.append(a)
    REVIEWERS.sort(key=lambda r: int(r[1:]))
    DEVS.sort(key=lambda d: int(d[3:]))
# Pipeline stages a finding moves through; index = how far it got.
STAGES = ["reported", "validated", "dev", "coord", "agreed"]
STATE_STAGE = {"reported": 0, "validating": 0, "valid": 1, "dev": 2, "coord": 3, "rev": 3,
               "agreed": 4}
STATE_LABEL = {"reported": ("waiting for coordinator", 245), "validating": ("coordinator validating", 75),
               "valid": ("queued for dev", 177), "invalid": ("rejected", 240),
               "duplicate": ("duplicate", 240), "dev": ("dev fixing + review loop", 220),
               "coord": ("coordinator checking fix", 75), "rev": ("reviewer checking fix", 117),
               "agreed": ("AGREED ✔", 46), "unresolved": ("UNRESOLVED ✘", 196)}


# Review-loop steps a dev reports with `ev devN phase <id> <step>`. A step is a DEV phase (the dev works)
# or a REVIEW phase (the dev's commit-reviewer has the change); approved and blocked stand apart.
PHASE_COL = {"dev": 220, "review": 39, "approved": 46, "blocked": 196}
PHASE_TAG = {"dev": "DEV", "review": "REVIEW", "approved": "APPROVED", "blocked": "BLOCKED"}


def phase_kind(step):
    return "review" if step.startswith("review") else step if step in ("approved", "blocked") else "dev"


def load():
    try:
        with open(EV) as f:
            evs = [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        return []
    roster(evs)
    return evs


def readt(p):
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return ""


GIB = 1 << 30


def meminfo():
    """Bytes from /proc/meminfo for the fields the machine panel shows."""
    m = {}
    for l in readt("/proc/meminfo").splitlines():
        k, _, v = l.partition(":")
        if k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            m[k] = int(v.split()[0]) * 1024
    return m


def slots():
    """Test slots: (count, held) where held lists the live $SW/slots/<n>.json; a file whose pid is gone is free."""
    try:
        count = int(readt(os.path.join(D, "slots", "count")).strip())
    except ValueError:
        count = 0
    held = []
    for p in sorted(glob.glob(os.path.join(D, "slots", "*.json"))):
        j = readj(p)
        if isinstance(j, dict) and str(j.get("pid", "")).isdigit() and os.path.exists(f"/proc/{j['pid']}"):
            cwd = str(j.get("cwd", ""))
            rel = os.path.relpath(cwd, D) if cwd.startswith(D + "/") else ""
            # tlock --as NAME records the holder; older slot files only have the directory it ran in
            j["who"] = str(j.get("agent") or "") or (rel.split("/")[0] if rel else os.path.basename(cwd.rstrip("/")) or "?")
            held.append(j)
    return count, held


def machine():
    """Memory, swap, load and test slots: what the terminal strip and the web page's /sys.json show."""
    m = meminfo()
    try:
        load = [float(x) for x in readt("/proc/loadavg").split()[:3]]
    except ValueError:
        load = [0.0, 0.0, 0.0]
    count, held = slots()
    return {"mem_total": m.get("MemTotal", 0), "mem_avail": m.get("MemAvailable", 0),
            "swap_total": m.get("SwapTotal", 0), "swap_used": m.get("SwapTotal", 0) - m.get("SwapFree", 0),
            "load": load, "cpus": os.cpu_count() or 1, "slot_count": count,
            "slots": [{k: j.get(k) for k in ("slot", "pid", "start", "cwd", "cmd", "who", "agent")} for j in held]}


def mem_col(avail):
    return 196 if avail < GIB else 214 if avail < 2 * GIB else 46


def load_col(load, cpus):
    return 196 if load > 1.5 * cpus else 214 if load > cpus else 46


def vis(s):
    """Visible length, ignoring ANSI escapes."""
    n, esc = 0, False
    for ch in s:
        if ch == "\033":
            esc = True
        elif esc and ch.isalpha():
            esc = False
        elif not esc:
            n += 1
    return n


def vcut(s, n):
    """Cut s to n visible columns, keeping ANSI escapes intact."""
    if vis(s) <= n:
        return s
    out, k, esc = [], 0, False
    for ch in s:
        if ch == "\033":
            esc = True
        if esc or k < n - 1:
            out.append(ch)
            k += not esc
        if esc and ch.isalpha():
            esc = False
    return "".join(out) + R + "…"


def cut(s, n):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def ago(t, now):
    s = int(now - t)
    return f"{s}s" if s < 60 else f"{s // 60}m" if s < 3600 else f"{s // 3600}h{s % 3600 // 60:02d}m"


def box(title, lines, w, col):
    out = [fg(col) + "╭─ " + B + title + R + fg(col) + " " + "─" * max(0, w - vis(title) - 5) + "╮" + R]
    for l in lines:
        l = vcut(l, w - 4)
        pad = max(0, w - 4 - vis(l))
        out.append(fg(col) + "│ " + R + l + " " * pad + fg(col) + " │" + R)
    out.append(fg(col) + "╰" + "─" * (w - 2) + "╯" + R)
    return out


# Stages of the whole swarm, logged by the dispatcher with `ev dispatcher stage - "<stage> <detail>"`.
STAGE_COL = {"setup": 245, "reviewing": 81, "paused": 208, "wind-down": 172, "final-review": 177, "final-fix": 220,
             "merge": 214, "done": 46, "stopped": 160}
ENDED = ("done", "stopped")  # stages after which nobody works and no pass runs
OPEN = ("reported", "validating", "valid", "dev", "coord", "rev")  # finding states that still need work
QUIET = 20 * 60  # an agent counted as working with no event of its own for this long is flagged quiet


def stage_name(msg):
    """(stage, detail) of a stage event, with natural spellings normalised: "final review" -> final-review."""
    w = msg.split()
    if not w:
        return "", ""
    name, rest = w[0].lower(), w[1:]
    if name in ("final", "wind") and rest and rest[0].lower() in ("review", "fix", "down"):
        name, rest = f"{name}-{rest[0].lower()}", rest[1:]
    name = {"pause": "paused", "stop": "stopped", "winddown": "wind-down", "finished": "done"}.get(name, name)
    return name, " ".join(rest)


def pass_event(e, passes):
    """Apply `ev dispatcher pass - "R9 23 started|clean|not clean|finished|stopped"` to passes[R9]."""
    w = e["msg"].split()
    if len(w) < 3 or not w[1].isdigit():
        return None
    r, n, what = w[0], int(w[1]), " ".join(w[2:])
    p = passes.setdefault(r, {"pass": n, "clean": 0, "state": "running", "t": e["t"]})
    p["t"] = e["t"]
    if what == "started":
        p.update({"pass": n, "state": "running"})
    elif what == "clean":
        p["clean"] += 1
    elif what == "not clean":
        p["clean"] = 0
    elif what in ("finished", "stopped"):
        p["state"] = what
    return r if what == "started" else None


def state_of(evs):
    agents, focus, findings, order, notes = {}, {}, {}, [], []
    stage, passes = None, {}
    for e in evs:
        a, k, i = e["agent"], e["kind"], e["id"]
        if k == "focus":
            focus[a] = e["msg"]
            continue
        if k == "note":
            notes.append(e)
            continue
        if k == "stage":
            stage = e
            continue
        if k == "pass":
            r = pass_event(e, passes)
            if r in agents:
                agents[r]["done"] = False  # a fresh reviewer took the area over
            continue
        st = agents.setdefault(a, {"msg": "", "t": e["t"], "done": False, "n": 0, "busy": False, "fid": "", "seen": e["t"]})
        st["seen"] = e["t"]
        owner = a[:-len("-reviewer")] if a.startswith("dev") and a.endswith("-reviewer") else ""
        if owner and owner in agents:
            agents[owner]["seen"] = e["t"]  # a dev's commit-reviewer at work counts as the dev's activity
        if k == "test":
            continue
        if k == "stopped":  # the dispatcher stopped the agent, or found its session gone
            st.update(msg="stopped: " + e["msg"], t=e["t"], kind=k, stopped=True, busy=False)
            continue
        st["stopped"] = False
        if k in ("status", "reported", "done", "idle"):
            st.update(msg=e["msg"], t=e["t"], fid=i, kind=k)
        elif k == "phase":
            st.update(t=e["t"], fid=i or st["fid"])
        if a.startswith("R"):
            st["done"] = st["done"] or k == "done"
        else:
            # a verdict as the last status (the final reviewer's "APPROVED ...") ends the agent's work
            st["busy"] = k not in ("idle", "done") and not (k == "status" and e["msg"].split()[:1] in (["APPROVED"], ["NOT"]))
        if k == "reported":
            st["n"] += 1
        if i:
            f = findings.setdefault(i, {"state": "reported", "sev": "", "title": "", "round": 0,
                                         "by": i.split("-")[0], "t": e["t"], "hist": []})
            if i not in order:
                order.append(i)
            if k == "reported":
                f["title"] = e["msg"]
            if k == "phase":
                steps = f.setdefault("steps", [])
                if not steps or steps[-1] != e["msg"]:
                    steps.append(e["msg"])
                f["dev"] = a
            elif a == "dispatcher" and k in STATE_LABEL:
                f["state"], f["t"] = k, e["t"]
                f["hist"].append((e["t"], k, e["msg"]))
                if k == "valid":
                    f["sev"] = e["msg"].split()[0] if e["msg"] else ""
                if k == "dev":
                    f["round"] += 1
                    f["steps"] = []  # a new hand-off starts a new review loop
            elif a in DEVS or a == "coordinator" or a.startswith("R"):
                f["last"] = (a, e["msg"], e["t"])
                if a in DEVS:
                    f["dev"] = a
    return agents, focus, findings, order, notes, stage, passes


def area(r, agents, findings, passes, sname=""):
    """A reviewer's area: (label, colour, finished, open findings, agreed findings)."""
    mine = [f for f in findings.values() if f["by"] == r]
    opn = sum(f["state"] in OPEN for f in mine)
    agreed = sum(f["state"] == "agreed" for f in mine)
    st, p = agents.get(r), passes.get(r)
    verifying = next((i for i, f in findings.items() if f["by"] == r and f["state"] == "rev"), None)
    if p and p["state"] in ("finished", "stopped"):
        return f"area {p['state']} after pass {p['pass']}", 46, True, opn, agreed
    if p and sname in ENDED:
        return f"pass {p['pass']} ended with the swarm ({sname})", 245, True, opn, agreed
    if verifying:
        return f"verifying the fix of {verifying}", 117, False, opn, agreed
    if not st:
        return "not started", 240, False, opn, agreed
    if st["done"] and opn:
        return f"kept for VERIFY, {opn} open", 117, False, opn, agreed
    if st["done"]:
        return ("pass done, waiting for the next" if p else "done"), 46, not p, opn, agreed
    return "", 220, False, opn, agreed


def track(f, tick):
    stage = STATE_STAGE.get(f["state"])
    if stage is None:
        return fg(240) + "· · · · · ·" + R
    dots = []
    for n in range(len(STAGES)):
        if n < stage or f["state"] == "agreed":
            dots.append(fg(46) + "●" + R)
        elif n == stage:
            col = PHASE_COL[phase_kind(steps_of(f)[-1])] if f["state"] == "dev" and steps_of(f) else 220
            dots.append(fg(col) + B + ("◉" if tick % 2 else "●") + R)
        else:
            dots.append(fg(238) + "○" + R)
    return (fg(238) + "─" + R).join(dots)


def waiting(evs, mc, now):
    """Agents whose test run waits for a slot: a tlock "wait" event not yet followed by its start or give-up.
    While a slot is free, a wait older than 10 s was stopped without logging (tlock retries every 3 s)."""
    w = {}
    for e in evs:
        if e.get("kind") == "test":
            if e["msg"] == "wait":
                w[e["agent"]] = e["t"]
            else:
                w.pop(e["agent"], None)
    free = len(mc["slots"]) < mc["slot_count"]
    return sorted(a for a, t in w.items() if not (free and now - t > 10))


def machine_line(mc, now):
    """One line: memory available (amber < 2 GB, red < 1 GB), swap, load vs CPUs, and who holds the test slots."""
    g = lambda b: f"{b / GIB:.1f}G"
    ma, l1 = mc["mem_avail"], mc["load"][0]
    held = ", ".join(f"{B}{j['who']}{R} {cut(str(j.get('cmd', '')).replace('./test/run-tests.sh', 'tests'), 18)} "
                     f"{DIM}{ago(j['start'], now) if isinstance(j.get('start'), (int, float)) else '?'}{R}"
                     for j in mc["slots"])
    return (f" Machine  mem {fg(mem_col(ma))}{B}{g(ma)}{R} avail of {g(mc['mem_total'])}"
            f"   swap {g(mc['swap_used'])}/{g(mc['swap_total'])}"
            f"   load {fg(load_col(l1, mc['cpus']))}{B}{l1:.2f}{R}/{mc['cpus']} cpu"
            f"   slots {B}{len(mc['slots'])}/{mc['slot_count']}{R}" + (f": {held}" if held else " free"))


def steps_of(f):
    """The finding's review-loop steps this round; before any phase event, guess from the dev's status."""
    if f.get("steps"):
        return f["steps"]
    if not f.get("dev"):
        return []
    msg = f.get("last", ("", "", 0))[1] if f.get("last", ("",))[0] == f["dev"] else ""
    return ["review"] if msg.lower().startswith("review") else ["implement"]


def ptag(step):
    """Coloured DEV / REVIEW badge for the current step."""
    k = phase_kind(step)
    return bgc(PHASE_COL[k]) + fg(16) + B + f" {PHASE_TAG[k]} " + R


def chain(steps, n):
    """implement › test › review-1 with the current (last) step highlighted, oldest steps dropped to fit n."""
    def build(st, more):
        parts = [DIM + x + R for x in st[:-1]] + [fg(PHASE_COL[phase_kind(st[-1])]) + B + "▶" + st[-1] + R]
        return (DIM + "… › " + R if more else "") + (DIM + " › " + R).join(parts)
    lo = 0
    while lo < len(steps) - 1 and vis(build(steps[lo:], lo)) > n:
        lo += 1
    return build(steps[lo:], lo) if steps else ""


def agent_state(a, st, findings, sname, now, held):
    """(state, label) of an agent: working, quiet (working but no event for QUIET), waiting (a dev whose finding
    moved on), paused, stopped or idle. The dispatcher logs `ev AGENT stopped - "<why>"` when it stops one."""
    if not st:
        return "idle", "not started"
    if st.get("stopped"):
        return "stopped", st["msg"]
    if sname in ENDED:
        return "idle", f"swarm {sname}"
    if sname == "paused":
        return "paused", "paused with the swarm"
    if a.startswith("R") and a[1:].isdigit():
        working = not st["done"] or any(f["by"] == a and f["state"] == "rev" for f in findings.values())
    elif a in DEVS:
        f = findings.get(st["fid"])
        if st["busy"] and f and f["state"] not in ("reported", "validating", "valid", "dev"):
            return "waiting", f"waiting for its next finding ({st['fid']} is {f['state']})"
        working = st["busy"]
    else:
        working = st["busy"]
    if not working:
        return "idle", ""
    if a not in held and now - st.get("seen", st["t"]) > QUIET:
        return "quiet", f"quiet for {ago(st.get('seen', st['t']), now)}: no event, no test run; stopped or stalled?"
    return "working", ""


NOTE = ""  # a warning shown under the header, e.g. an edited dash.py that was refused


def render(evs, tick):
    now = time.time()
    W = shutil.get_terminal_size((130, 50)).columns
    H = shutil.get_terminal_size((130, 50)).lines
    agents, focus, findings, order, notes, stage, passes = state_of(evs)
    until = int(CFG.get("until_clean") or 0)
    t0 = evs[0]["t"] if evs else now
    out = []

    # Header
    wave = "".join(PULSE[(tick + n) % len(PULSE)] for n in range(8))
    title = f" ◆ {TITLE}   {wave}   ⏱ {ago(t0, now)}   {len(evs)} events   "
    mode = f" REPEATED PASSES · until {until} clean in a row " if until else " ONE PASS "
    title = title[:max(0, W - len(mode))]
    out.append(B + bgc(54) + fg(231) + title + bgc(31 if until else 136) + fg(231) + mode + bgc(54)
               + " " * max(0, W - len(title) - len(mode)) + R)

    # Progress
    valid = [f for f in findings.values() if f["state"] not in ("reported", "validating", "invalid", "duplicate")]
    done = sum(f["state"] == "agreed" for f in findings.values())
    rej = sum(f["state"] in ("invalid", "duplicate") for f in findings.values())
    unres = sum(f["state"] == "unresolved" for f in findings.values())
    sname, sdetail = stage_name(stage["msg"]) if stage else ("", "")
    revdone = sum(1 for r in REVIEWERS if area(r, agents, findings, passes, sname)[2])
    head = (f" Areas {B}{revdone}/{len(REVIEWERS)}{R} finished   Findings {B}{len(findings)}{R} "
            f"({fg(46)}{done} agreed{R}, {fg(177)}{len(valid) - done - unres} in pipeline{R}, "
            f"{fg(240)}{rej} rejected{R}, {fg(196)}{unres} unresolved{R})")
    if vis(head) > W:  # narrow terminal: the same counts, shorter
        head = (f" {B}{revdone}/{len(REVIEWERS)}{R} areas · {B}{len(findings)}{R} findings: {fg(46)}{done} agreed{R}, "
                f"{fg(177)}{len(valid) - done - unres} open{R}, {fg(240)}{rej} rej{R}, {fg(196)}{unres} unres{R}")
    tail = f" {done}/{len(valid)}"
    inline = W - vis(head) - len(tail) - 3 >= 10  # room for the bar on the same line
    bw = min(40, W - vis(head) - len(tail) - 3 if inline else W - len(tail) - 2)
    fill = int(bw * done / len(valid)) if valid else 0
    bar = fg(46) + "█" * fill + fg(238) + "░" * (bw - fill) + R + tail
    out += [head + "   " + bar] if inline else [head, " " + bar]
    sev_counts = {}
    for f in valid:
        sev_counts[f["sev"]] = sev_counts.get(f["sev"], 0) + 1
    chips = " ".join(bgc(SEV.get(s, 240)) + fg(16) + B + f" {s} {n} " + R
                     for s, n in sorted(sev_counts.items(), key=lambda x: list(SEV).index(x[0]) if x[0] in SEV else 9))
    out.append(" Severity " + (chips or DIM + "none confirmed yet" + R))
    mc = machine()
    wt = waiting(evs, mc, now)
    out.append(machine_line(mc, now) + (f"   {fg(208)}{B}{len(wt)} waiting for a slot{R}: {', '.join(wt)}" if wt else ""))
    held = {re.sub(r"^(dev\d+)-reviewer$", r"\1", str(j.get("who", ""))) for j in mc["slots"]}
    others = sorted(a for a in {e["agent"] for e in evs} - set(DEVS) - set(REVIEWERS) - {"coordinator", "dispatcher", "dash"}
                    if not (a.startswith("dev") and a.endswith("-reviewer")))  # a dev's commit-reviewer only logs test runs
    roster_all = REVIEWERS + ["coordinator"] + DEVS + others
    states = {a: agent_state(a, agents.get(a), findings, sname, now, held) for a in roster_all}
    cnt = lambda s: [a for a in roster_all if states[a][0] == s]
    summ = (f" Agents   {fg(220)}{B}{len(cnt('working'))} working{R}"
            + (f"   {fg(208)}{B}{len(cnt('quiet'))} quiet {QUIET // 60}m+{R} ({', '.join(cnt('quiet'))})" if cnt("quiet") else "")
            + (f"   {fg(196)}{len(cnt('stopped'))} stopped{R}" if cnt("stopped") else "")
            + (f"   {fg(208)}{len(cnt('paused'))} paused{R}" if cnt("paused") else "")
            + f"   {len(cnt('waiting'))} waiting for a finding   {DIM}{len(cnt('idle'))} idle{R}"
            + f"   test slots {B}{len(mc['slots'])}/{mc['slot_count']}{R}")
    out.append(summ)

    # Stage and narration
    if NOTE:
        out.append(" " + fg(196) + "⚠ " + R + cut(NOTE, W - 4))
    if stage:
        name, detail = sname, sdetail
        col = STAGE_COL.get(name, 245)
        out.append(" " + bgc(col) + fg(16) + B + f" {name.upper()} " + R + f" {B}{ago(stage['t'], now)}{R}  "
                   + cut(detail, W - len(name) - 16))
    else:
        out.append(" " + DIM + "stage not logged yet (ev dispatcher stage - \"<stage> <detail>\")" + R)
    for n in notes[-3:]:
        out.append(" " + fg(177) + "▶ " + R + IT + cut(n["msg"], W - 14) + R + DIM + f"  {ago(n['t'], now)} ago" + R)
    out.append("")

    # Agents column + findings column
    leftw = 58 if W >= 130 else W
    left = []
    for idx, r in enumerate(REVIEWERS):
        st = agents.get(r)
        col = agent_col(r)
        if not st:
            icon, msg = fg(240) + "·" + R, DIM + "not started" + R
        elif st["done"]:
            icon, msg = fg(46) + "✔" + R, DIM + cut(st["msg"], leftw - 32) + R
        else:
            icon, msg = fg(220) + SPIN[(tick + idx) % len(SPIN)] + R, cut(st["msg"], leftw - 32)
        label, lcol, fin, opn, agreed = area(r, agents, findings, passes, sname)
        if fin:
            icon = fg(46) + "✔" + R
        if label:
            msg = fg(lcol) + label + R
        ast, alab = states[r]
        if ast in ("quiet", "stopped", "paused") and not fin:
            icon, msg = fg(208 if ast != "stopped" else 196) + {"quiet": "?", "stopped": "■", "paused": "‖"}[ast] + R, fg(208) + alab + R
        flag = fg(214 if opn else 240) + f"{opn}o" + R + DIM + "/" + R + fg(46 if agreed else 240) + f"{agreed}a" + R
        p = passes.get(r)
        ptxt = (fg(81) + f"p{p['pass']}" + R + DIM + f" {p['clean']}{'/' + str(until) if until else ''}✓ " + R) if p else ""
        left.append(f"{icon} {fg(col)}{B}{r:<3}{R} {fg(col)}{cut(focus.get(r, ''), 13):<13}{R} {flag} {ptxt}{msg}")
    left.append("")
    nw = max([11] + [len(a) for a in others])
    for a in ["coordinator"] + DEVS + others:
        st = dict(agents.get(a, {"msg": "not started" if a in DEVS else "waiting", "t": now, "busy": False, "fid": ""}))
        ast, alab = states[a]
        st["busy"] = ast == "working"
        if alab:
            st["msg"] = alab
        icon = {"working": fg(220) + SPIN[tick % len(SPIN)], "quiet": fg(208) + "?", "stopped": fg(196) + "■",
                "paused": fg(208) + "‖"}.get(ast, fg(240) + "◌") + R
        fid = (B + f"{st['fid']:<6}" + R) if st["fid"] else " " * 6
        steps = steps_of(findings[st["fid"]]) if a in DEVS and st["busy"] and st["fid"] in findings else []
        tag = ptag(steps[-1]) + " " if steps else ""
        left.append(f"{icon} {fg(agent_col(a))}{B}{a.upper():<{nw}}{R} {fid} {tag}"
                    f"{cut(st['msg'], leftw - 20 - nw - vis(tag))} {DIM}{ago(st['t'], now)}{R}")
        if steps:
            left.append("  " + DIM + "loop " + R + chain(steps, leftw - 11))
    lbox = box("AGENTS", left, leftw, 99)

    rightw = W - leftw - 1 if W >= 130 else W
    right = [DIM + "stages: reported─validated─dev fix+review─coordinator─agreed" + R]
    active = [i for i in order if findings[i]["state"] not in ("invalid", "duplicate")]
    for i in active:
        f = findings[i]
        lab, col = STATE_LABEL.get(f["state"], (f["state"], 245))
        steps = steps_of(f) if f["state"] == "dev" else []
        if steps:
            k = phase_kind(steps[-1])
            lab, col = f"{f['dev']} {PHASE_TAG[k]}" + (f" {steps[-1]}" if k in ("dev", "review") else ""), PHASE_COL[k]
        elif f["state"] == "dev" and f.get("dev"):
            lab, col = f["dev"] + " fixing", agent_col(f["dev"])
        sev = f["sev"] or "?"
        sv = bgc(SEV.get(sev, 238)) + fg(16) + B + f"{sev[:4]:^4}" + R
        rnd = f" r{f['round']}/4" if f["round"] else "     "
        right.append(f"{sv} {B}{i:<6}{R} {track(f, tick)} {fg(col)}{cut(lab, 24):<24}{R}{rnd}")
        right.append(f"      {DIM}{cut(f['title'], rightw - 12)}{R}")
    gone = [i for i in order if findings[i]["state"] in ("invalid", "duplicate")]
    if gone:
        right.append(DIM + "rejected: " + ", ".join(gone) + R)
    rbox = box("FINDINGS PIPELINE", right, rightw, 177)

    if W >= 130:
        for n in range(max(len(lbox), len(rbox))):
            l = lbox[n] if n < len(lbox) else " " * leftw
            r = rbox[n] if n < len(rbox) else ""
            out.append(l + " " + r)
    else:
        out += lbox + rbox

    # Spotlight: the finding the dev is on, else the newest one in the pipeline.
    busy = [i for i in order if findings[i]["state"] in ("dev", "coord", "rev")]
    spot = max(busy, key=lambda i: findings[i].get("last", ("", "", 0))[2], default=None)
    spot = spot or next((i for i in reversed(order) if findings[i]["state"] in ("valid", "validating")), None)
    if spot:
        f = findings[spot]
        q = readj(os.path.join(D, "queue", spot + ".json"))
        v = readt(os.path.join(D, "val", spot + ".txt"))
        lines = []
        wrapw = W - 6
        lines.append(f"{B}{cut(q.get('title', f['title']), wrapw)}{R}")
        loc = f"{q.get('file', '')}:{q.get('line', '')}" if q.get("file") else ""
        lines.append(f"{DIM}reported by {f['by']} · {loc} · state: {STATE_LABEL.get(f['state'], ('?',))[0]}"
                     f"{' · round ' + str(f['round']) + '/4' if f['round'] else ''}{R}")
        for label, txt, col in (("Problem", q.get("description", ""), 252),
                                ("Exploit", q.get("failure_scenario", ""), 210),
                                ("Verdict", " ".join(v.split("\n")[0:1]), 75)):
            if txt:
                wrapped = textwrap.wrap(" ".join(str(txt).split()), wrapw - 10)[:3 if label != "Verdict" else 1]
                for n, wl in enumerate(wrapped):
                    lines.append((fg(col) + B + f"{label:<9}" + R if n == 0 else " " * 9) + wl)
        if f.get("steps") or f["state"] == "dev":
            steps = steps_of(f)
            if steps:
                lines.append(fg(PHASE_COL[phase_kind(steps[-1])]) + B + f"{'Loop':<9}" + R + ptag(steps[-1]) + " "
                             + chain(steps, wrapw - 22))
        if "last" in f:
            a, m, t = f["last"]
            lines.append(fg(agent_col(a)) + B + f"{'Now':<9}" + R + f"{a}: {cut(m, wrapw - 30)} {DIM}{ago(t, now)} ago{R}")
        out += box(f"SPOTLIGHT {spot}", lines, W, SEV.get(f["sev"], 99))

    # Live feed fills the rest
    room = max(4, H - len(out) - 3)
    feed = []
    for e in evs[-room:]:
        a = e["agent"]
        col = agent_col(a)
        k = e["kind"]
        kcol = {"reported": 214, "valid": 46, "invalid": 240, "agreed": 46, "unresolved": 196,
                "note": 177, "done": 46}.get(k, 245)
        if k == "phase":
            kcol = PHASE_COL[phase_kind(e["msg"])]
        feed.append(f"{DIM}{time.strftime('%H:%M:%S', time.localtime(e['t']))}{R} {fg(col)}{a:<11}{R} "
                    f"{fg(kcol)}{k:<10}{R} {e['id']:<6} {cut(e['msg'], W - 48)}")
    out += box("LIVE", feed, W, 240)
    return "\n".join(vcut(l, W) for l in out)


def stamp():
    """Identity of dash.py and swarm.json on disk, so an edit of either can be noticed."""
    out = []
    for p in (SRC, CFG_PATH):
        try:
            st = os.stat(p)
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def selftest():
    """Render one frame with the dash.py now on disk in a child; return (ok, last stderr line)."""
    code = ("import importlib.util as u, sys; s = u.spec_from_file_location('d', sys.argv[1]); "
            "m = u.module_from_spec(s); s.loader.exec_module(m); m.render(m.load(), 0)")
    try:
        r = subprocess.run([sys.executable, "-c", code, SRC], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    err = r.stderr.strip().split("\n")[-1] if r.stderr.strip() else ""
    return r.returncode == 0, err


def restart():
    """Replace this process with a fresh dash.py, leaving the terminal clean."""
    sys.stdout.write("\033[?25h\033[2J\033[H")
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable] + sys.argv)


def maybe_reload(cur, seen):
    """Re-exec once an edited dash.py has been stable for a tick and renders; refuse it otherwise."""
    global NOTE
    new = stamp()
    if new == cur:
        return cur, None
    if new != seen:
        return cur, new  # just changed; wait a tick in case the write is still in progress
    ok, err = selftest()
    if ok:
        restart()
    NOTE = f"edited dash.py refused, still running the old one: {err or 'self-test failed'}"
    return new, None


def main():
    sys.stdout.write("\033[?25l\033[2J")
    tick, cur, seen = 0, stamp(), None
    try:
        while True:
            try:
                frame = render(load(), tick)
            except Exception:
                frame = fg(196) + B + "dashboard render failed, retrying:" + R + "\n" + traceback.format_exc()
            sys.stdout.write("\033[H" + frame.replace("\n", "\033[K\n") + "\033[K\033[J")
            sys.stdout.flush()
            tick += 1
            time.sleep(0.5)
            cur, seen = maybe_reload(cur, seen)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\n")


if __name__ == "__main__":
    main()
