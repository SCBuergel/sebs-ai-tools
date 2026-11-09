#!/usr/bin/env python3
"""Persist the Claude Code sessions running on this machine and bring them back after a restart.

  snapshot            record every session (interactive and background) to the state folder
  restore             open one terminal window with a tab per recorded session that is not running
  install/uninstall   the snapshot timer (systemd --user) and the login autostart, for this user only
  status              what is installed, the latest snapshot, whether this boot was restored
  exclude/include     keep a session (by name or id prefix) out of restores, or put it back

Stdlib only. A snapshot holds ids, names, folders, flags and models: no environment, no tokens.
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time

HOME = pathlib.Path(os.environ.get("HOME", str(pathlib.Path.home())))
STATE = HOME / ".local/state/reboot-resume"
SNAPS = STATE / "snapshots"
CONFIG = HOME / ".config/reboot-resume/config.json"
INSTALL_DIR = HOME / ".local/share/reboot-resume"
INSTALLED_SCRIPT = INSTALL_DIR / "reboot-resume.py"
AUTOSTART = HOME / ".config/autostart/reboot-resume.desktop"
UNIT_DIR = HOME / ".config/systemd/user"
SERVICE = "reboot-resume-snapshot.service"
TIMER = "reboot-resume-snapshot.timer"
CLAUDE_DIR = HOME / ".claude"

KEEP_LAST = 20          # snapshots kept regardless of boot
KEEP_BOOTS = 5          # plus the newest snapshot of each of this many boots
TIMER_GRACE = 30 * 60   # the timer waits this long after boot for the restore before snapshotting anyway
# Flags that select or start a session; restore supplies its own --resume.
DROP_FLAGS = {"--resume": "opt", "-r": "opt", "--continue": 0, "-c": 0, "--session-id": 1,
              "--fork-session": 0, "--bg": 0, "--background": 0, "--print": 0, "-p": 0}


def log(msg):
    print(msg, file=sys.stderr)


def boot_id():
    try:
        return pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return "unknown"


def uptime():
    try:
        return float(pathlib.Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def encode_dir(path):
    """The folder name Claude Code stores a cwd's transcripts under."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def clean_flags(args):
    out, i = [], 0
    while i < len(args):
        a = args[i]
        name = a.split("=", 1)[0]
        kind = DROP_FLAGS.get(name)
        if kind is None:
            out.append(a)
        elif "=" not in a and (kind == 1 or (kind == "opt" and i + 1 < len(args)
                                             and not args[i + 1].startswith("-"))):
            i += 1
        i += 1
    return out


def flag_value(flags, *names):
    for i, a in enumerate(flags):
        for n in names:
            if a == n and i + 1 < len(flags):
                return flags[i + 1]
            if a.startswith(n + "="):
                return a.split("=", 1)[1]
    return None


def tail_lines(path, block=1 << 20, limit=64 << 20):
    """Yield a file's lines newest first, reading at most `limit` bytes from the end."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        pos, read, buf = f.tell(), 0, b""
        while pos > 0 and read < limit:
            n = min(block, pos)
            pos -= n
            read += n
            f.seek(pos)
            buf = f.read(n) + buf
            lines = buf.split(b"\n")
            buf = lines[0]
            for line in reversed(lines[1:]):
                if line.strip():
                    yield line
        if pos == 0 and buf.strip():
            yield buf


def transcript_facts(path):
    """The cwds a transcript recorded (newest first, distinct) and the model it last used."""
    cwds, model = [], None
    try:
        for raw in tail_lines(path):
            if b'"cwd"' not in raw and b'"model"' not in raw:
                continue
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            c = e.get("cwd")
            if isinstance(c, str) and c not in cwds and len(cwds) < 8:
                cwds.append(c)
            if model is None and e.get("type") == "assistant":
                m = (e.get("message") or {}).get("model")
                if isinstance(m, str) and m and not m.startswith("<"):
                    model = m
            if model and cwds:
                break
    except OSError:
        pass
    return cwds, model


def find_transcript(sid, hint=None):
    if hint and pathlib.Path(hint).is_file():
        return pathlib.Path(hint)
    found = sorted((CLAUDE_DIR / "projects").glob(f"*/{sid}.jsonl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return found[0] if found else None


def resume_dir(transcript, cwds, fallbacks):
    """Pick the folder `claude --resume` must run in: the one the transcript is stored under."""
    cands = [c for c in cwds + fallbacks if c]
    if transcript is not None:
        want = transcript.parent.name
        for c in cands:
            if encode_dir(c) == want and os.path.isdir(c):
                return c, None
    for c in cands:
        if os.path.isdir(c):
            note = None if transcript is None else f"no folder of this session matches {transcript.parent.name}"
            return c, note
    return (cands[0] if cands else str(HOME)), "folder gone"


def proc_args(pid):
    try:
        raw = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    argv = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
    for i, a in enumerate(argv):
        base = os.path.basename(a)
        if base == "claude" or base.endswith("cli.js") or base == "claude.exe":
            return argv[i + 1:]
    return None


def proc_start(pid):
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def live_sessions():
    """Running sessions from `claude agents --json` and ~/.claude/sessions/<pid>.json, by session id."""
    found = {}
    try:
        out = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True,
                             timeout=30).stdout
        for a in json.loads(out or "[]"):
            if a.get("sessionId"):
                found[a["sessionId"]] = a
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    for f in (CLAUDE_DIR / "sessions").glob("*.json"):
        try:
            a = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        sid, pid = a.get("sessionId"), a.get("pid")
        if not sid or sid in found or not pid or proc_args(pid) is None:
            continue
        if a.get("procStart") and proc_start(pid) not in (None, str(a["procStart"])):
            continue  # pid reused by another process since
        found[sid] = a
    return found


def running_ids():
    ids = set(live_sessions())
    for p in pathlib.Path("/proc").iterdir():
        if p.name.isdigit():
            args = proc_args(p.name) or []
            for i, a in enumerate(args):
                if a in ("--resume", "-r", "--session-id") and i + 1 < len(args):
                    ids.add(args[i + 1])
                elif a.startswith(("--resume=", "--session-id=")):
                    ids.add(a.split("=", 1)[1])
    return ids


def jobs():
    for f in sorted((CLAUDE_DIR / "jobs").glob("*/state.json")):
        try:
            j = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(j, dict) and j.get("sessionId"):
            yield j


def user_services():
    """Running services from the user's own unit files: listed for the user, never started by restore."""
    try:
        out = subprocess.run(["systemctl", "--user", "list-units", "--type=service", "--state=running",
                              "--no-legend", "--plain"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    res = []
    for line in out.splitlines():
        unit = line.split()[0] if line.split() else ""
        if not unit.endswith(".service") or unit == SERVICE:
            continue
        try:
            show = subprocess.run(["systemctl", "--user", "show", "-p", "FragmentPath,UnitFileState", unit],
                                  capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        props = dict(l.split("=", 1) for l in show.splitlines() if "=" in l)
        if props.get("FragmentPath", "").startswith(str(UNIT_DIR)):
            res.append({"unit": unit, "enabled": props.get("UnitFileState") == "enabled"})
    return res


def collect():
    sessions = {}

    def add(sid, kind, name, cwd, flags, transcript_hint, running):
        t = find_transcript(sid, transcript_hint)
        cwds, model = transcript_facts(t) if t else ([], None)
        folder, note = resume_dir(t, cwds, [cwd])
        flags = clean_flags(flags)
        if model and not flag_value(flags, "--model"):
            flags += ["--model", model]
        sessions[sid] = {
            "id": sid, "name": name or os.path.basename(folder.rstrip("/")) or sid[:8], "named": bool(name),
            "kind": kind, "cwd": folder, "flags": flags, "model": flag_value(flags, "--model") or model,
            "transcript": str(t) if t else None, "note": note, "running": running,
            "mtime": t.stat().st_mtime if t else 0,
        }

    live = live_sessions()
    for sid, a in live.items():
        args = proc_args(a["pid"]) if a.get("pid") else None
        add(sid, a.get("kind") or "interactive", a.get("name"), a.get("cwd"), args or [], None, True)
    for j in jobs():
        sid = j["sessionId"]
        if sid in sessions:
            sessions[sid]["kind"] = "background"
            continue
        add(sid, "background", j.get("name"), j.get("cwd"), list(j.get("respawnFlags") or []),
            j.get("linkScanPath"), False)

    # A session resumed as a new copy keeps the name: restore only the newest of each name,
    # unless several of them are running now.
    kept, superseded = [], []
    by_name = {}
    for s in sessions.values():
        if s["named"]:
            by_name.setdefault(s["name"], []).append(s)
        else:
            kept.append(s)
    for group in by_name.values():
        live_ones = [s for s in group if s["running"]]
        winners = live_ones or [max(group, key=lambda s: s["mtime"])]
        kept += winners
        superseded += [{"id": s["id"], "name": s["name"], "by": winners[0]["id"]}
                       for s in group if s not in winners]
    kept.sort(key=lambda s: (s["kind"] != "interactive", s["name"].lower()))
    for s in kept:
        s.pop("running")
    return {"version": 1, "taken_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "boot_id": boot_id(), "host": socket.gethostname(), "sessions": kept,
            "superseded": superseded, "user_services": user_services()}


# --- snapshot files -----------------------------------------------------------

def snapshot_files():
    if not SNAPS.is_dir():
        return []
    snaps = []
    for f in sorted(SNAPS.glob("*.json")):
        try:
            snaps.append((f, json.loads(f.read_text())))
        except (OSError, ValueError):
            continue
    return snaps  # oldest first; names sort by time


def essence(snap):
    return [(s["id"], s["name"], s["cwd"], s["flags"], s["kind"]) for s in snap["sessions"]]


def write_snapshot(snap, force=False):
    SNAPS.mkdir(parents=True, exist_ok=True)
    snaps = snapshot_files()
    if not force and snaps and snaps[-1][1].get("boot_id") == snap["boot_id"] \
            and essence(snaps[-1][1]) == essence(snap):
        return None
    name = time.strftime("%Y%m%dT%H%M%S") + f"-{time.time_ns()}.json"  # sorts by time
    tmp = SNAPS / f".{name}.tmp"
    tmp.write_text(json.dumps(snap, indent=1) + "\n")
    os.replace(tmp, SNAPS / name)
    prune()
    return SNAPS / name


def prune():
    snaps = snapshot_files()
    keep = {f for f, _ in snaps[-KEEP_LAST:]}
    boots = []
    for f, s in reversed(snaps):
        b = s.get("boot_id")
        if b not in boots:
            boots.append(b)
            if len(boots) <= KEEP_BOOTS:
                keep.add(f)
    for f, _ in snaps:
        if f not in keep:
            f.unlink(missing_ok=True)


def restored_marker(boot=None):
    return STATE / f"restored-{boot or boot_id()}"


def pick_snapshot(which):
    snaps = snapshot_files()
    if which == "latest":
        return snaps[-1] if snaps else (None, None)
    if which:
        p = pathlib.Path(which)
        return p, json.loads(p.read_text())
    cur = boot_id()
    for f, s in reversed(snaps):
        if s.get("boot_id") != cur:
            return f, s
    return None, None


# --- config -------------------------------------------------------------------

def load_config():
    cfg = {"exclude": [], "extras": [], "start_extras": False}
    try:
        cfg.update(json.loads(CONFIG.read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=1) + "\n")
    os.replace(tmp, CONFIG)


def matches(s, key):
    k = key.lower()
    return s["name"].lower() == k or (len(k) >= 8 and s["id"].lower().startswith(k))


# --- restore ------------------------------------------------------------------

def tab_script(cwd, argv):
    cmd = shlex.join(["claude"] + argv)
    return (f'export PATH="$HOME/.local/bin:$PATH"\n'
            f"if cd -- {shlex.quote(cwd)}; then\n"
            f"  echo {shlex.quote('[reboot-resume] ' + cmd)}\n"
            f"  {cmd}\n"
            f'  rc=$?; echo; echo "[reboot-resume] claude ended with exit code $rc. This tab stays open."\n'
            f"else\n"
            f"  echo {shlex.quote('[reboot-resume] folder ' + cwd + ' is gone; not resumed.')}\n"
            f"fi\n"
            f"exec bash -i\n")


def resume_argv(s):
    return ["--resume", s["id"]] + s["flags"]


def tab_path(i, title):
    return STATE / "tabs" / f"{i:02d}-{re.sub(r'[^A-Za-z0-9]+', '-', title).strip('-') or 'tab'}.sh"


def terminal_argv(tabs):
    """One xfce4-terminal window, one tab per (title, cwd, script file). -e takes one string per
    tab (-x would swallow the rest of the command line)."""
    argv = ["xfce4-terminal"]
    for i, (title, cwd, path) in enumerate(tabs):
        argv += ["--window" if i == 0 else "--tab", "--title", title]
        if os.path.isdir(cwd):
            argv += ["--working-directory", cwd]
        argv += ["-e", shlex.join(["bash", "-l", str(path)])]
    return argv


def write_tabs(tabs):
    """Write each tab's script under the state folder; return (title, cwd, path) per tab."""
    d = STATE / "tabs"
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("*.sh"):
        old.unlink()
    out = []
    for i, (title, cwd, script) in enumerate(tabs):
        path = tab_path(i, title)
        path.write_text(script)
        out.append((title, cwd, path))
    return out


def plan(snap, cfg, only, running):
    todo, skipped = [], []
    for s in snap["sessions"]:
        if only and not any(matches(s, k) for k in only):
            continue
        if any(matches(s, k) for k in cfg["exclude"]):
            skipped.append((s, "excluded"))
        elif s["id"] in running:
            skipped.append((s, "already running"))
        else:
            todo.append(s)
    return todo, skipped


def wait_for_network(limit):
    end = time.time() + limit
    while time.time() < end:
        try:
            socket.create_connection(("api.anthropic.com", 443), timeout=5).close()
            return True
        except OSError:
            time.sleep(5)
    return False


def cmd_restore(a):
    if a.auto:
        time.sleep(a.delay)
        if not wait_for_network(a.network_wait):
            log("[reboot-resume] network not reachable; opening the tabs anyway")
    path, snap = pick_snapshot(a.snapshot)
    if snap is None:
        log("No snapshot from an earlier boot. Use --snapshot latest to restore from this boot's.")
        if a.auto:
            mark_restored()
        return 0 if a.auto else 1
    cfg = load_config()
    todo, skipped = plan(snap, cfg, a.only, running_ids())
    extras = cfg["extras"] if cfg.get("start_extras") else []
    if a.print:
        for s in todo:
            print(f"cd {shlex.quote(s['cwd'])} && {shlex.join(['claude'] + resume_argv(s))}")
        return 0
    tabs = [(s["name"], s["cwd"], tab_script(s["cwd"], resume_argv(s))) for s in todo]
    lines = [f"cd {shlex.quote(s['cwd'])} && {shlex.join(['claude'] + resume_argv(s))}" for s in todo]
    tabs += [(e.get("name", "extra"), e.get("cwd", str(HOME)),
              f"cd -- {shlex.quote(e.get('cwd', str(HOME)))} && {e['cmd']}\nexec bash -i\n")
             for e in extras if e.get("cmd")]
    lines += [f"cd {shlex.quote(e.get('cwd', str(HOME)))} && {e['cmd']}" for e in extras if e.get("cmd")]
    if a.dry_run or not a.auto:
        print(f"Snapshot: {path} (taken {snap['taken_at']})")
        for s in todo:
            note = f"  [{s['note']}]" if s.get("note") else ""
            print(f"  restore  {s['name']:<24} {s['kind']:<11} {s['cwd']}{note}")
        for s, why in skipped:
            print(f"  skip     {s['name']:<24} {why}")
        for x in snap.get("superseded", []):
            print(f"  skip     {x['name']:<24} superseded by {x['by'][:8]}")
        for e in cfg["extras"]:
            state = "started as a tab" if cfg.get("start_extras") else "not started (start_extras is off)"
            print(f"  extra    {e.get('name', '?'):<24} {state}")
        for u in snap.get("user_services", []):
            how = "enabled, starts by itself" if u["enabled"] else "NOT enabled, will not come back"
            print(f"  service  {u['unit']:<24} {how}")
    if not tabs:
        print("Nothing to open.")
        if not a.dry_run:
            mark_restored()
        return 0
    if a.dry_run:
        argv = terminal_argv([(t, c, tab_path(i, t)) for i, (t, c, _) in enumerate(tabs)])
        print("\nEach tab's script runs (and then keeps a shell open):")
        print("\n".join(f"  {tab_path(i, t).name}: {line}" for i, ((t, _, _), line) in enumerate(zip(tabs, lines))))
        print("\nWould run:\n" + shlex.join(argv))
        return 0
    argv = terminal_argv(write_tabs(tabs))
    with open(STATE / "restore.log", "a") as lf:
        lf.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {path}\n{shlex.join(argv)}\n")
        subprocess.Popen(argv, stdout=lf, stderr=lf, stdin=subprocess.DEVNULL, start_new_session=True)
    mark_restored()
    print(f"Opened {len(tabs)} tab(s).")
    return 0


def mark_restored():
    STATE.mkdir(parents=True, exist_ok=True)
    for old in STATE.glob("restored-*"):
        old.unlink(missing_ok=True)
    restored_marker().touch()


# --- other commands -----------------------------------------------------------

def cmd_snapshot(a):
    if a.timer and AUTOSTART.exists() and not restored_marker().exists() and uptime() < TIMER_GRACE:
        log("[reboot-resume] waiting for this boot's restore before snapshotting")
        return 0
    snap = collect()
    f = write_snapshot(snap, force=not a.timer)
    if not a.quiet:
        print(f"{len(snap['sessions'])} session(s) -> {f or 'unchanged, not written'}")
        for s in snap["sessions"]:
            print(f"  {s['name']:<24} {s['kind']:<11} {s['id'][:8]}  {s['cwd']}")
    return 0


def unit_text():
    script = shlex.quote(str(INSTALLED_SCRIPT))
    service = ("[Unit]\nDescription=Record running Claude Code sessions for reboot-resume\n\n"
               f"[Service]\nType=oneshot\nExecStart=/usr/bin/python3 {script} snapshot --timer --quiet\n")
    timer = ("[Unit]\nDescription=Record running Claude Code sessions every few minutes\n\n"
             "[Timer]\nOnBootSec=3min\nOnUnitActiveSec=3min\n\n[Install]\nWantedBy=timers.target\n")
    desktop = ("[Desktop Entry]\nType=Application\nName=Restore Claude Code sessions\n"
               f"Exec=/usr/bin/python3 \"{INSTALLED_SCRIPT}\" restore --auto\n"
               "X-GNOME-Autostart-enabled=true\nNoDisplay=true\n")
    return service, timer, desktop


def systemctl(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def cmd_install(a):
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(__file__, INSTALLED_SCRIPT)
    INSTALLED_SCRIPT.chmod(0o755)
    service, timer, desktop = unit_text()
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    (UNIT_DIR / SERVICE).write_text(service)
    (UNIT_DIR / TIMER).write_text(timer)
    AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
    AUTOSTART.write_text(desktop)
    if not CONFIG.exists():
        save_config(load_config())
    for step in (["daemon-reload"], ["enable", "--now", TIMER]):
        r = systemctl(*step)
        if r.returncode:
            log(f"systemctl --user {' '.join(step)} failed: {r.stderr.strip()}")
            return 1
    print(f"Installed: {INSTALLED_SCRIPT}, {UNIT_DIR / TIMER}, {AUTOSTART}, config {CONFIG}")
    return 0


def cmd_uninstall(a):
    systemctl("disable", "--now", TIMER)
    for f in (UNIT_DIR / SERVICE, UNIT_DIR / TIMER, AUTOSTART, INSTALLED_SCRIPT):
        f.unlink(missing_ok=True)
    systemctl("daemon-reload")
    if INSTALL_DIR.is_dir() and not any(INSTALL_DIR.iterdir()):
        INSTALL_DIR.rmdir()
    if a.purge:
        shutil.rmtree(STATE, ignore_errors=True)
        shutil.rmtree(CONFIG.parent, ignore_errors=True)
    print("Uninstalled" + (" and removed snapshots and config." if a.purge
                           else f". Snapshots kept in {STATE}, config in {CONFIG}."))
    return 0


def cmd_status(a):
    print(f"script     {'installed' if INSTALLED_SCRIPT.exists() else 'not installed'}: {INSTALLED_SCRIPT}")
    print(f"autostart  {'yes' if AUTOSTART.exists() else 'no'}: {AUTOSTART}")
    print(f"timer      {systemctl('is-active', TIMER).stdout.strip() or 'unknown'}")
    print(f"this boot  {'restored' if restored_marker().exists() else 'not restored yet'}")
    snaps = snapshot_files()
    if snaps:
        f, s = snaps[-1]
        print(f"latest     {f} ({s['taken_at']}, {len(s['sessions'])} sessions)")
    print(f"exclude    {', '.join(load_config()['exclude']) or '-'}")
    return 0


def cmd_exclude(a, add):
    cfg = load_config()
    for k in a.keys:
        if add and k not in cfg["exclude"]:
            cfg["exclude"].append(k)
        elif not add and k in cfg["exclude"]:
            cfg["exclude"].remove(k)
    save_config(cfg)
    print(f"exclude: {', '.join(cfg['exclude']) or '-'}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="record the sessions now")
    s.add_argument("--timer", action="store_true", help="as run by the timer: write only on change")
    s.add_argument("--quiet", action="store_true")
    r = sub.add_parser("restore", help="open the recorded sessions that are not running")
    r.add_argument("--dry-run", action="store_true", help="print the plan and the exact command")
    r.add_argument("--print", action="store_true", help="print copy-paste `cd … && claude --resume …` lines")
    r.add_argument("--snapshot", help="'latest' or a file; default: the newest from an earlier boot")
    r.add_argument("--only", nargs="+", metavar="NAME|ID", help="restore only these sessions")
    r.add_argument("--auto", action="store_true", help="as run at login: wait, then open")
    r.add_argument("--delay", type=int, default=15, help="seconds to wait first with --auto")
    r.add_argument("--network-wait", type=int, default=180, help="seconds to wait for the network with --auto")
    sub.add_parser("install", help="install the timer and the login autostart for this user")
    u = sub.add_parser("uninstall", help="remove the timer and the autostart")
    u.add_argument("--purge", action="store_true", help="also delete snapshots and config")
    sub.add_parser("status")
    for n in ("exclude", "include"):
        x = sub.add_parser(n, help=f"{n} sessions by name or id prefix (8+ characters)")
        x.add_argument("keys", nargs="+")
    a = p.parse_args(argv)
    return {"snapshot": cmd_snapshot, "restore": cmd_restore, "install": cmd_install,
            "uninstall": cmd_uninstall, "status": cmd_status,
            "exclude": lambda a: cmd_exclude(a, True), "include": lambda a: cmd_exclude(a, False)}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
