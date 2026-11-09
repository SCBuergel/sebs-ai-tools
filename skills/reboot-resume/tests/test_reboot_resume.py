"""Tests for reboot-resume.py.

Run: python3 -m pytest <skill folder>/tests
Each test gets a throwaway home; nothing touches the real ~/.claude, systemd or the display.
"""
import importlib.util
import json
import os
import pathlib
import shlex
import subprocess
import time
import types

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "reboot-resume.py"


@pytest.fixture
def rr(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("reboot_resume", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    home = tmp_path / "home"
    for name, rel in [("HOME", ""), ("STATE", ".local/state/reboot-resume"),
                      ("SNAPS", ".local/state/reboot-resume/snapshots"),
                      ("CONFIG", ".config/reboot-resume/config.json"),
                      ("INSTALL_DIR", ".local/share/reboot-resume"),
                      ("INSTALLED_SCRIPT", ".local/share/reboot-resume/reboot-resume.py"),
                      ("AUTOSTART", ".config/autostart/reboot-resume.desktop"),
                      ("UNIT_DIR", ".config/systemd/user"), ("CLAUDE_DIR", ".claude")]:
        monkeypatch.setattr(m, name, home / rel if rel else home)
    (home / ".claude/projects").mkdir(parents=True)
    (home / ".claude/jobs").mkdir()
    (home / ".claude/sessions").mkdir()
    m.LIVE = {}
    monkeypatch.setattr(m, "live_sessions", lambda: dict(m.LIVE))
    monkeypatch.setattr(m, "proc_args", lambda pid: m.ARGS.get(pid))
    monkeypatch.setattr(m, "running_ids", lambda: set(m.LIVE))
    monkeypatch.setattr(m, "user_services", lambda: [])
    monkeypatch.setattr(m, "boot_id", lambda: m.BOOT)
    m.ARGS, m.BOOT = {}, "boot-1"
    return m


def transcript(rr, cwd, sid, lines=None, folder=None):
    d = rr.CLAUDE_DIR / "projects" / (folder or rr.encode_dir(str(cwd)))
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    lines = lines if lines is not None else [
        {"type": "user", "cwd": str(cwd)},
        {"type": "assistant", "cwd": str(cwd), "message": {"model": "claude-opus-5-5"}}]
    p.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return p


def job(rr, sid, name, cwd, flags, scan=None):
    d = rr.CLAUDE_DIR / "jobs" / sid[:8]
    d.mkdir(parents=True)
    st = {"sessionId": sid, "name": name, "cwd": str(cwd), "respawnFlags": flags,
          "providerEnv": {"SECRET_TOKEN": "x"}, "bridgeSessionId": "cse_secret"}
    if scan:
        st["linkScanPath"] = str(scan)
    (d / "state.json").write_text(json.dumps(st))


def args(**kw):
    base = dict(dry_run=False, print=False, snapshot=None, only=None, auto=False, delay=0,
                network_wait=0, timer=False, quiet=True, purge=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


# --- pure helpers ---------------------------------------------------------------

def test_encode_dir_matches_claude_projects_naming(rr):
    assert rr.encode_dir("/home/user/nym-explorer/.claude/worktrees/build") == \
        "-home-user-nym-explorer--claude-worktrees-build"


def test_clean_flags_drops_session_selection_only(rr):
    flags = ["--resume", "abc", "--dangerously-skip-permissions", "-c", "--remote-control", "My name",
             "--session-id", "x", "--bg", "-r", "--model", "opus", "--resume=zzz"]
    assert rr.clean_flags(flags) == ["--dangerously-skip-permissions", "--remote-control", "My name",
                                     "--model", "opus"]


def test_tail_lines_handles_lines_longer_than_a_block(rr, tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_bytes(b"first\n" + b"x" * 5000 + b"\nlast\n")
    assert list(rr.tail_lines(p, block=64)) == [b"last", b"x" * 5000, b"first"]


def test_transcript_facts_takes_newest_cwd_and_model(rr, tmp_path):
    p = transcript(rr, "/a", "s1", [
        {"type": "assistant", "cwd": "/a", "message": {"model": "old"}},
        {"type": "assistant", "cwd": "/b", "message": {"model": "new"}},
        {"type": "assistant", "cwd": "/b", "message": {"model": "<synthetic>"}}])
    cwds, model = rr.transcript_facts(p)
    assert cwds[0] == "/b" and "/a" not in cwds[:1] and model == "new"


# --- resume folder ----------------------------------------------------------------

def test_resume_dir_is_the_folder_the_transcript_is_stored_under(rr, tmp_path):
    main, wt = tmp_path / "repo", tmp_path / "repo/.claude/worktrees/build"
    wt.mkdir(parents=True)
    t = transcript(rr, wt, "s1", [{"cwd": str(main)}, {"cwd": str(wt)}, {"cwd": str(main)}])
    assert rr.resume_dir(t, [str(main), str(wt)], [str(main)]) == (str(wt), None)


def test_resume_dir_falls_back_to_an_existing_folder_with_a_note(rr, tmp_path):
    there = tmp_path / "there"
    there.mkdir()
    t = transcript(rr, "/gone", "s1")
    folder, note = rr.resume_dir(t, ["/gone"], [str(there)])
    assert folder == str(there) and note


def test_resume_dir_reports_a_missing_folder(rr):
    assert rr.resume_dir(None, [], ["/nope"]) == ("/nope", "folder gone")


# --- collect ----------------------------------------------------------------------

def test_collect_interactive_takes_flags_from_the_process_and_model_from_transcript(rr, tmp_path):
    cwd = tmp_path / "proj"
    cwd.mkdir()
    transcript(rr, cwd, "i1")
    rr.LIVE["i1"] = {"pid": 42, "cwd": str(cwd), "kind": "interactive", "name": "home"}
    rr.ARGS[42] = ["--dangerously-skip-permissions", "--remote-control"]
    s = rr.collect()["sessions"][0]
    assert s["flags"] == ["--dangerously-skip-permissions", "--remote-control", "--model", "claude-opus-5-5"]
    assert (s["name"], s["kind"], s["cwd"]) == ("home", "interactive", str(cwd))


def test_collect_background_keeps_respawn_flags_and_their_model(rr, tmp_path):
    cwd = tmp_path / "bg"
    cwd.mkdir()
    t = transcript(rr, cwd, "b1")
    job(rr, "b1", "Bridge", cwd, ["--remote-control", "-n", "Bridge", "--model", "opus"], scan=t)
    snap = rr.collect()
    s = snap["sessions"][0]
    assert s["flags"] == ["--remote-control", "-n", "Bridge", "--model", "opus"]
    assert s["kind"] == "background" and s["transcript"] == str(t)
    assert "SECRET" not in json.dumps(snap) and "cse_secret" not in json.dumps(snap)


def test_collect_keeps_only_the_newest_copy_of_a_name(rr, tmp_path):
    cwd = tmp_path / "turk"
    cwd.mkdir()
    old, new = transcript(rr, cwd, "old1"), transcript(rr, cwd, "new1")
    os.utime(old, (1, 1))
    job(rr, "old1aaaa", "Turk", cwd, [])
    job(rr, "new1bbbb", "Turk", cwd, [])
    # job ids differ from transcript ids above only in the folder name; point them right
    for sid, t in (("old1aaaa", old), ("new1bbbb", new)):
        st = rr.CLAUDE_DIR / "jobs" / sid[:8] / "state.json"
        d = json.loads(st.read_text())
        d["linkScanPath"] = str(t)
        st.write_text(json.dumps(d))
    snap = rr.collect()
    assert [s["id"] for s in snap["sessions"]] == ["new1bbbb"]
    assert snap["superseded"][0]["id"] == "old1aaaa"


def test_collect_keeps_two_running_sessions_of_one_name(rr, tmp_path):
    for sid in ("r1", "r2"):
        rr.LIVE[sid] = {"pid": None, "cwd": str(tmp_path), "name": "same"}
    assert len(rr.collect()["sessions"]) == 2


def test_running_background_session_stays_background(rr, tmp_path):
    rr.LIVE["b1"] = {"pid": None, "cwd": str(tmp_path), "name": "x", "kind": "interactive"}
    job(rr, "b1", "x", tmp_path, [])
    assert rr.collect()["sessions"][0]["kind"] == "background"


# --- snapshot files ------------------------------------------------------------------

def test_timer_snapshot_is_written_only_on_change(rr, tmp_path):
    rr.LIVE["i1"] = {"pid": None, "cwd": str(tmp_path), "name": "a"}
    assert rr.write_snapshot(rr.collect()) is not None
    assert rr.write_snapshot(rr.collect()) is None
    rr.LIVE["i2"] = {"pid": None, "cwd": str(tmp_path), "name": "b"}
    assert rr.write_snapshot(rr.collect()) is not None


def test_restore_uses_the_newest_snapshot_of_an_earlier_boot(rr, tmp_path):
    rr.LIVE["i1"] = {"pid": None, "cwd": str(tmp_path), "name": "before"}
    rr.write_snapshot(rr.collect())
    rr.BOOT = "boot-2"
    rr.LIVE.clear()
    rr.write_snapshot(rr.collect())
    f, snap = rr.pick_snapshot(None)
    assert [s["name"] for s in snap["sessions"]] == ["before"]
    assert rr.pick_snapshot("latest")[1]["sessions"] == []


def test_prune_keeps_recent_files_and_one_per_boot(rr, monkeypatch, tmp_path):
    monkeypatch.setattr(rr, "KEEP_LAST", 3)
    for i in range(8):
        rr.BOOT = "boot-old" if i < 2 else "boot-new"
        rr.LIVE = {f"s{i}": {"pid": None, "cwd": str(tmp_path), "name": f"n{i}"}}
        rr.write_snapshot(rr.collect())
    snaps = rr.snapshot_files()
    assert len(snaps) == 4 and snaps[0][1]["boot_id"] == "boot-old"
    assert rr.pick_snapshot(None)[1]["sessions"][0]["name"] == "n1"


def test_timer_waits_for_the_restore_after_boot(rr, monkeypatch, tmp_path):
    rr.AUTOSTART.parent.mkdir(parents=True)
    rr.AUTOSTART.write_text("x")
    monkeypatch.setattr(rr, "uptime", lambda: 60.0)
    rr.LIVE["i1"] = {"pid": None, "cwd": str(tmp_path), "name": "a"}
    rr.cmd_snapshot(args(timer=True))
    assert rr.snapshot_files() == []
    rr.mark_restored()
    rr.cmd_snapshot(args(timer=True))
    assert len(rr.snapshot_files()) == 1


# --- restore ----------------------------------------------------------------------

@pytest.fixture
def recorded(rr, tmp_path):
    a, b = tmp_path / "a dir", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    rr.LIVE = {"id-a-0001": {"pid": None, "cwd": str(a), "name": "alpha"},
               "id-b-0002": {"pid": None, "cwd": str(b), "name": "SEQs"}}
    rr.write_snapshot(rr.collect())
    rr.BOOT = "boot-2"
    rr.LIVE = {}
    return a, b


def test_dry_run_prints_one_window_with_a_tab_per_session(rr, recorded, capsys, monkeypatch):
    popen = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popen.append(a))
    assert rr.cmd_restore(args(dry_run=True)) == 0
    out = capsys.readouterr().out
    cmd = shlex.split(out.split("Would run:\n")[1])
    assert cmd[0] == "xfce4-terminal" and cmd.count("--window") == 1 and cmd.count("--tab") == 1
    assert "-x" not in cmd
    first = shlex.split(cmd[cmd.index("-e") + 1])
    assert first[:2] == ["bash", "-l"] and first[2] == str(rr.tab_path(0, "alpha"))
    assert f"01-SEQs.sh: cd {recorded[1]} && claude --resume id-b-0002" in out
    assert not popen and not rr.restored_marker().exists() and not (rr.STATE / "tabs").exists()


def test_tab_script_runs_in_bash_and_keeps_the_tab(rr, tmp_path):
    there = tmp_path / "it's here"
    there.mkdir()
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "claude").write_text('#!/bin/sh\necho "ARGS:$*" > "$OUT"; pwd >> "$OUT"; exit 3\n')
    (bin_ / "claude").chmod(0o755)
    script = rr.tab_script(str(there), ["--resume", "x", "--remote-control", "a b"])
    script = script.replace("exec bash -i", "echo KEPT")
    out = tmp_path / "out"
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"PATH": f"{bin_}:/usr/bin:/bin", "HOME": str(tmp_path), "OUT": str(out)})
    assert out.read_text().splitlines() == ["ARGS:--resume x --remote-control a b", str(there)]
    assert "exit code 3" in r.stdout and "KEPT" in r.stdout


def test_tab_script_for_a_missing_folder_does_not_run_claude(rr, tmp_path):
    script = rr.tab_script(str(tmp_path / "gone"), ["--resume", "x"]).replace("exec bash -i", "")
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert "is gone" in r.stdout and "claude ended" not in r.stdout


def test_restore_skips_excluded_and_running_sessions(rr, recorded, capsys):
    rr.cmd_exclude(args(keys=["seqs"]), True)
    rr.LIVE = {"id-a-0001": {}}
    rr.cmd_restore(args(dry_run=True))
    out = capsys.readouterr().out
    assert "already running" in out and "excluded" in out and "Nothing to open." in out


def test_restore_only_and_print_give_copy_paste_lines(rr, recorded, capsys):
    rr.cmd_restore(args(print=True, only=["alpha"]))
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    cd, rest = lines[0].split(" && ")
    assert shlex.split(cd)[1] == str(recorded[0]) and rest.startswith("claude --resume id-a-0001")


def test_restore_launches_once_and_marks_the_boot(rr, recorded, monkeypatch):
    popen = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: popen.append(argv))
    assert rr.cmd_restore(args()) == 0
    assert len(popen) == 1 and popen[0][0] == "xfce4-terminal"
    assert rr.restored_marker().exists()
    script = rr.tab_path(0, "alpha").read_text()
    assert "claude --resume id-a-0001" in script and "exec bash -i" in script
    assert sorted(f.name for f in (rr.STATE / "tabs").iterdir()) == ["00-alpha.sh", "01-SEQs.sh"]


def test_auto_restore_without_an_earlier_boot_marks_and_succeeds(rr, monkeypatch):
    monkeypatch.setattr(rr, "wait_for_network", lambda limit: True)
    assert rr.cmd_restore(args(auto=True)) == 0
    assert rr.restored_marker().exists()


def test_extras_are_listed_but_not_started_by_default(rr, recorded, capsys):
    rr.save_config({"exclude": [], "extras": [{"name": "turk", "cwd": "/tmp", "cmd": "make serve"}],
                    "start_extras": False})
    rr.cmd_restore(args(dry_run=True))
    out = capsys.readouterr().out
    assert "not started" in out and "make serve" not in out


# --- install ----------------------------------------------------------------------

def test_install_and_uninstall_touch_only_their_files(rr, monkeypatch):
    calls = []
    monkeypatch.setattr(rr, "systemctl", lambda *a: calls.append(a) or types.SimpleNamespace(
        returncode=0, stdout="", stderr=""))
    assert rr.cmd_install(args()) == 0
    desktop = rr.AUTOSTART.read_text()
    assert f'"{rr.INSTALLED_SCRIPT}" restore --auto' in desktop
    assert "snapshot --timer" in (rr.UNIT_DIR / rr.SERVICE).read_text()
    assert ("enable", "--now", rr.TIMER) in calls
    rr.cmd_uninstall(args())
    assert not rr.AUTOSTART.exists() and not (rr.UNIT_DIR / rr.TIMER).exists()
    assert not rr.INSTALLED_SCRIPT.exists() and rr.CONFIG.exists()
    assert ("disable", "--now", rr.TIMER) in calls


# --- review fixes -------------------------------------------------------------------

def test_clean_flags_never_replays_the_prompt(rr):
    assert rr.clean_flags(["fix the bug", "--model", "opus"]) == ["--model", "opus"]
    assert rr.clean_flags(["--dangerously-skip-permissions", "do it", "--remote-control"]) == \
        ["--dangerously-skip-permissions", "--remote-control"]
    assert rr.clean_flags(["--add-dir", "/a", "/b", "--verbose", "go"]) == ["--add-dir", "/a", "/b", "--verbose"]
    assert rr.clean_flags(["-n", "my name", "--remote-control", "rc name", "-w", "wt"]) == \
        ["-n", "my name", "--remote-control", "rc name"]


def test_collect_finds_the_stored_folder_behind_newer_worktree_entries(rr, tmp_path):
    main, wt = tmp_path / "repo", tmp_path / "repo/.claude/worktrees/wt"
    wt.mkdir(parents=True)
    lines = [{"type": "user", "cwd": str(main)}] + \
        [{"type": "assistant", "cwd": str(wt), "message": {"model": "m"}}] * 5
    t = transcript(rr, main, "w1", lines)
    assert rr.transcript_facts(t)[0][-1] == str(main)
    rr.LIVE["w1"] = {"pid": None, "cwd": str(wt), "name": "wt"}
    s = rr.collect()["sessions"][0]
    assert s["cwd"] == str(main) and s["note"] is None


def test_second_auto_restore_in_one_boot_opens_nothing(rr, recorded, monkeypatch):
    popen = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: popen.append(argv))
    monkeypatch.setattr(rr, "wait_for_network", lambda limit: True)
    rr.cmd_restore(args(auto=True))
    rr.cmd_restore(args(auto=True))
    assert len(popen) == 1


def test_old_background_jobs_are_left_out_as_stale(rr, tmp_path):
    cwd = tmp_path / "old"
    cwd.mkdir()
    t = transcript(rr, cwd, "o1")
    os.utime(t, (1, 1))
    job(rr, "o1", "Old", cwd, [], scan=t)
    rr.LIVE["o1x"] = {"pid": None, "cwd": str(cwd), "name": "live"}
    snap = rr.collect()
    assert [s["name"] for s in snap["sessions"]] == ["live"]
    assert snap["stale"][0]["name"] == "Old"


def test_extras_stay_out_of_only_and_appear_in_print(rr, recorded, capsys):
    rr.save_config({"exclude": [], "extras": [{"name": "turk", "cwd": "/tmp", "cmd": "make serve"}],
                    "start_extras": True})
    rr.cmd_restore(args(print=True, only=["alpha"]))
    assert "make serve" not in capsys.readouterr().out
    rr.cmd_restore(args(print=True))
    assert "cd /tmp && make serve" in capsys.readouterr().out


def test_a_missing_terminal_is_reported_and_the_boot_not_marked(rr, recorded, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("xfce4-terminal")
    monkeypatch.setattr(subprocess, "Popen", boom)
    assert rr.cmd_restore(args()) == 1
    assert not rr.restored_marker().exists()
    assert "failed" in (rr.STATE / "restore.log").read_text()


def test_desktop_quote_escapes_for_the_exec_key(rr):
    assert rr.desktop_quote('/h/a "b" $c\\d 5%') == '"/h/a \\\\"b\\\\" \\\\$c\\\\\\\\d 5%%"'


def test_process_helpers_read_a_real_process(tmp_path, monkeypatch):
    """No stubs: a real process whose command line names `claude`, and its sessions file."""
    spec = importlib.util.spec_from_file_location("rr_raw", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.CLAUDE_DIR = tmp_path / ".claude"
    (m.CLAUDE_DIR / "sessions").mkdir(parents=True)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "claude").write_text('#!/bin/sh\necho "[]"\n')  # stands in for `claude agents --json`
    (bin_ / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:/usr/bin:/bin")
    # sh -c keeps its argv: [sh, -c, script, <$0>, args...], and $0 is named claude
    q = subprocess.Popen(["/bin/sh", "-c", "sleep 30", str(bin_ / "claude"), "--resume", "sid-456"])
    try:
        for _ in range(50):
            if m.proc_args(q.pid):
                break
            time.sleep(0.02)
        assert m.proc_args(q.pid) == ["--resume", "sid-456"]
        start = m.proc_start(q.pid)
        assert start and start.isdigit()
        sessions = m.CLAUDE_DIR / "sessions"
        (sessions / f"{q.pid}.json").write_text(json.dumps(
            {"pid": q.pid, "sessionId": "sid-file", "procStart": start}))
        (sessions / "1.json").write_text(json.dumps({"pid": 1, "sessionId": "sid-pid-1", "procStart": "0"}))
        assert set(m.live_sessions()) == {"sid-file"}
        assert {"sid-file", "sid-456"} <= m.running_ids()
    finally:
        q.kill()
        q.wait()
