"""Tests for swarm-init --py and the merge queue's test run. Run: python3 -m pytest <skill>/tests

Each test builds a throwaway repository and swarm under tmp_path; nothing outside it changes.
"""
import importlib.machinery
import json
import importlib.util
import os
import subprocess

import pytest

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def init_swarm(tmp_path, test_cmd, *extra):
    repo, sw = tmp_path / "repo", tmp_path / "sw"
    (repo / "src").mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.email", "t@example.com", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    (repo / "src" / "m.py").write_text("X = 1\n")
    git("add", ".", cwd=repo)
    git("commit", "-qm", "init", cwd=repo)
    subprocess.run([os.path.join(TOOLS, "swarm-init"), "--repo", str(repo), "--swarm", str(sw),
                    "--reviewers", "1", "--devs", "1", "--slots", "1", "--one-pass",
                    "--test-cmd", test_cmd, *extra],
                   cwd=repo, check=True, capture_output=True, env={**os.environ, "SWARM_TEST_MEM": "1G"})
    return repo, sw


def load_board(sw):
    loader = importlib.machinery.SourceFileLoader("board", str(sw / "board"))
    spec = importlib.util.spec_from_loader("board", loader)
    board = importlib.util.module_from_spec(spec)
    loader.exec_module(board)
    return board


def dev_commit(sw, text):
    (sw / "dev1" / "src" / "m.py").write_text(text)
    git("commit", "-qam", "[R1-1] change", cwd=sw / "dev1")
    return git("rev-parse", "HEAD", cwd=sw / "dev1")


def test_py_is_the_interpreter_every_agent_uses(tmp_path):
    _, sw = init_swarm(tmp_path, "true", "--py", "env PYTHONPATH=src python3")
    protocol = (sw / "PROTOCOL.md").read_text()
    assert "- Interpreter: `$SW/py` (it runs `env PYTHONPATH=src python3`)" in protocol
    out = subprocess.run([str(sw / "py"), "-c", "import m; print(m.X)"], cwd=sw / "dev1",
                         capture_output=True, text=True)
    assert out.stdout.strip() == "1"


def test_without_py_the_protocol_says_none_is_recorded(tmp_path):
    _, sw = init_swarm(tmp_path, "true")
    assert "- Interpreter: none recorded" in (sw / "PROTOCOL.md").read_text()
    assert not (sw / "py").exists()


@pytest.mark.parametrize("script,expect", [
    ("exit 0", None),
    ("exit 3", "and again on a rerun"),
    # fails the first time only: a flaky test
    ('test -e "$M" && exit 0; touch "$M"; exit 1', "FLAKY"),
])
def test_the_merge_queue_reruns_a_failed_suite_once(tmp_path, monkeypatch, script, expect):
    sleep = __import__("time").sleep
    monkeypatch.setattr("time.sleep", lambda s: sleep(min(s, 0.1)))  # the queue polls every 10 s
    marker = tmp_path / "ran-once"
    _, sw = init_swarm(tmp_path, f'M={marker}; {script}')
    board = load_board(sw)
    why = board.merge_one({"id": "R1-1", "sha": dev_commit(sw, "X = 2\n")}, board.cfg())
    if expect is None:
        assert why is None
    else:
        assert expect in why
    assert (sw / "mergeq" / "R1-1.log.rerun").exists() == (expect is not None)


def set_line(sw, path, text, msg, where="dev1"):
    (sw / where / path).write_text(text)
    git("commit", "-qam", msg, cwd=sw / where)
    return git("rev-parse", "HEAD", cwd=sw / where)


def advance_review(repo, text):
    """Another dev's change lands on swarm-review first."""
    (repo / "src" / "m.py").write_text(text)
    git("commit", "-qam", "[R2-1] other", cwd=repo)


def test_an_import_only_conflict_is_resolved_and_merged(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    repo, sw = init_swarm(tmp_path, "true")
    sha = set_line(sw, "src/m.py", "import os\nX = 1\n", "[R1-1] use os")
    advance_review(repo, "import sys\nX = 1\n")
    board = load_board(sw)
    assert board.merge_one({"id": "R1-1", "sha": sha}, board.cfg()) is None
    assert (sw / "merge" / "src" / "m.py").read_text() == "import sys\nimport os\nX = 1\n"
    # the rebased change differs from the approved one, but only in import lines
    assert "only import lines changed; the approval stands" in (sw / "events.jsonl").read_text()


def test_a_rebase_that_changes_only_the_context_goes_back(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    repo, sw = init_swarm(tmp_path, "true")
    body = "def g(x):\n    y = 1\n    z = 2\n    return x\n"
    (repo / "src" / "m.py").write_text(body)
    git("commit", "-qam", "four lines", cwd=repo)
    git("checkout", "-q", "-B", "swarm-dev1", "swarm-review", cwd=sw / "dev1")
    sha = set_line(sw, "src/m.py", body.replace("return x", "return check(x)"), "[R1-1] check")
    # another change renames g, two lines away: no conflict, but the approved change's context moved
    advance_review(repo, body.replace("def g", "def h"))
    board = load_board(sw)
    why = board.merge_one({"id": "R1-1", "sha": sha}, board.cfg())
    assert why and why.startswith("the rebase changed the approved change")


def test_an_import_conflict_that_rebinds_a_name_goes_back(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    repo, sw = init_swarm(tmp_path, "true")
    sha = set_line(sw, "src/m.py", "from b import f\nX = 1\n", "[R1-1] b.f")
    advance_review(repo, "from c import f\nX = 1\n")
    board = load_board(sw)
    assert board.merge_one({"id": "R1-1", "sha": sha}, board.cfg()).startswith("rebase conflict")


SORT = "python3 -c \"import sys; p='src/m.py'; l=open(p).read().splitlines(True); i=[x for x in l if x.startswith('import ')]; open(p,'w').write(''.join(sorted(i)+[x for x in l if x not in i]))\""


@pytest.mark.parametrize("fix,expect", [(SORT, "import os\nimport sys\nX = 1\n"), ("exit 1", None)])
def test_the_import_sorter_runs_after_a_resolution_and_must_pass(tmp_path, monkeypatch, fix, expect):
    monkeypatch.setattr("time.sleep", lambda s: None)
    repo, sw = init_swarm(tmp_path, "true", "--import-fix", fix)
    sha = set_line(sw, "src/m.py", "import os\nX = 1\n", "[R1-1] use os")
    advance_review(repo, "import sys\nX = 1\n")
    board = load_board(sw)
    why = board.merge_one({"id": "R1-1", "sha": sha}, board.cfg())
    if expect:
        assert why is None and git("show", "HEAD:src/m.py", cwd=sw / "merge") + "\n" == expect
    else:
        assert why.startswith("the import sorter failed (rc=1)")


@pytest.mark.parametrize("args", [("--clean-below", "Severe"), ("--max-passes", "many")])
def test_swarm_init_refuses_a_bad_pass_option(tmp_path, args):
    with pytest.raises(subprocess.CalledProcessError):
        init_swarm(tmp_path, "true", *args)


def test_change_without_imports_keeps_every_other_line(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    repo, sw = init_swarm(tmp_path, "true")
    board = load_board(sw)
    sha = set_line(sw, "src/m.py", "import os\nX = 1\n", "[R1-1] use os")
    old = git("rev-parse", "swarm-review", cwd=repo)
    lines = board.change_without_imports(str(sw / "dev1"), old, sha)
    assert "+import os" not in lines and " X = 1" in lines
    sha2 = set_line(sw, "src/m.py", "import os\nX = 9\n", "[R1-1] and more")
    assert ["-X = 1", "+X = 9"] == [l for l in board.change_without_imports(str(sw / "dev1"), old, sha2)
                                    if l[:1] in "+-" and not l.startswith(("+++", "---"))]


def test_any_other_conflict_goes_back(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    repo, sw = init_swarm(tmp_path, "true")
    sha = set_line(sw, "src/m.py", "import os\nX = 2\n", "[R1-1] two")
    advance_review(repo, "import sys\nX = 3\n")
    board = load_board(sw)
    why = board.merge_one({"id": "R1-1", "sha": sha}, board.cfg())
    assert why.startswith("rebase conflict") and "src/m.py" in why


@pytest.mark.parametrize("ours,theirs,clash", [
    ("from x import a, b, d", "from x import a, b, c", False),
    ("import a.b", "import a.c", False),
    ("import { a } from 'm';", "import { a, b } from 'm';", False),
    ("from c import f", "from b import f", True),
    ("from x import f as g", "from x import h as g", True),
    ("import a.b as c", "import a.d as c", True),
])
def test_only_a_name_bound_to_something_else_clashes(ours, theirs, clash):
    assert load_board_module().clashes([ours], [theirs]) is clash


def test_two_names_added_to_one_import_line_merge(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    repo, sw = init_swarm(tmp_path, "true")
    (repo / "src" / "m.py").write_text("from x import a, b\nX = 1\n")
    git("commit", "-qam", "base", cwd=repo)
    git("checkout", "-q", "-B", "swarm-dev1", "swarm-review", cwd=sw / "dev1")
    sha = set_line(sw, "src/m.py", "from x import a, b, c\nX = 1\n", "[R1-1] c")
    advance_review(repo, "from x import a, b, d\nX = 1\n")
    board = load_board(sw)
    assert board.merge_one({"id": "R1-1", "sha": sha}, board.cfg()) is None
    assert git("show", "HEAD:src/m.py", cwd=sw / "merge") == "from x import a, b, d\nfrom x import a, b, c\nX = 1"


def test_import_lines_are_recognised():
    board_imports = load_board_module().IMPORT_RE
    for line in ("import os", "import a.b as c, d", "from .x import y as z, w", "from x import *",
                 "import { a } from 'b';", "import x from \"y\""):
        assert board_imports.fullmatch(line), line
    for line in ("X = 1", "from x import (", "def f(): import os", "important = 1"):
        assert not board_imports.fullmatch(line), line


def load_board_module():
    loader = importlib.machinery.SourceFileLoader("board_src", os.path.join(TOOLS, "board"))
    spec = importlib.util.spec_from_loader("board_src", loader)
    board = importlib.util.module_from_spec(spec)
    loader.exec_module(board)
    return board


def ev(sw, agent, kind, fid, msg):
    subprocess.run([str(sw / "ev"), agent, kind, fid, msg], check=True)


@pytest.mark.parametrize("severity,state", [("Low", "clean"), ("Medium", "not clean")])
def test_a_pass_is_clean_below_medium(tmp_path, severity, state):
    _, sw = init_swarm(tmp_path, "true", "--until-clean", "2")
    board = load_board(sw)
    board.cmd_pass("R1", "start")
    (sw / "state" / "R1-1.json").write_text(json.dumps({
        "id": "R1-1", "area": "R1", "pass": 1, "state": "agreed", "severity": severity,
        "history": [{"state": "reported"}, {"state": "valid"}, {"state": "agreed"}]}))
    ev(sw, "R1", "done", "-", "pass 1")
    board.tick()
    assert board.passes()["R1"] == [state]


def test_an_area_stops_at_max_passes(tmp_path):
    _, sw = init_swarm(tmp_path, "true", "--until-clean", "2", "--max-passes", "2")
    board = load_board(sw)
    for n in (1, 2):
        board.cmd_pass("R1", "start")
        (sw / "state" / f"R1-{n}.json").write_text(json.dumps({
            "id": f"R1-{n}", "area": "R1", "pass": n, "state": "agreed", "severity": "High",
            "history": [{"state": "valid"}]}))
        ev(sw, "R1", "done", "-", f"pass {n}")
        board.tick()
    assert board.passes()["R1"] == ["not clean", "capped"]


def test_amend_appends_to_the_requirements_and_logs_it(tmp_path):
    _, sw = init_swarm(tmp_path, "true")
    board = load_board(sw)
    (sw / "state" / "R1-1.json").write_text(json.dumps({"id": "R1-1", "area": "R1", "state": "dev",
                                                       "owner": "dev1", "history": []}))
    (sw / "val" / "R1-1.txt").write_text("VALID R1-1 Low\nOption 1 must go.\n")
    board.cmd_amend("supervisor", "R1-1", "Option 1 stays allowed.")
    text = (sw / "val" / "R1-1.txt").read_text()
    assert text.startswith("VALID R1-1 Low\nOption 1 must go.\n\nAMENDED ")
    assert text.endswith("by supervisor, replacing anything above that it contradicts:\nOption 1 stays allowed.\n")
    last = json.loads((sw / "events.jsonl").read_text().splitlines()[-1])
    assert (last["agent"], last["kind"], last["id"]) == ("supervisor", "amend", "R1-1")


def test_a_stopped_event_never_closes_a_new_pass(tmp_path):
    _, sw = init_swarm(tmp_path, "true", "--until-clean", "2")
    board = load_board(sw)
    board.cmd_pass("R1", "start")
    ev(sw, "R1", "done", "-", "pass 1")
    board.tick()
    assert board.passes()["R1"] == ["clean"]
    board.cmd_pass("R1", "start")
    # the pass-1 agent ends after pass 2 started: pass 2 stays open
    ev(sw, "R1", "stopped", "-", "pass-1 agent stopped")
    out = board.tick()
    assert board.passes()["R1"] == ["clean", "started"]
    assert any(l.startswith("QUIET reviewer R1") for l in out)
    ev(sw, "R1", "focus", "-", "pass 2 agent")
    assert not any(l.startswith("QUIET reviewer R1") for l in board.tick())
    # only pass 2's own done closes it
    ev(sw, "R1", "done", "-", "pass 2")
    board.tick()
    assert board.passes()["R1"] == ["clean", "finished"]


def tlock(sw, *args, cwd=None):
    return subprocess.run([str(sw / "tlock"), "--as", "dev1", *args], cwd=cwd or sw / "dev1",
                          capture_output=True, text=True, env={**os.environ, "SWARM_TEST_MEM": "1G"})


def test_tlock_reuses_a_pass_on_the_same_tree_but_never_a_failure(tmp_path):
    _, sw = init_swarm(tmp_path, "true")
    count = tmp_path / "runs"
    cmd = ["sh", "-c", f'echo run >> {count}; echo out; test -e {tmp_path}/fail && exit 1; exit 0']
    assert tlock(sw, *cmd).returncode == 0
    second = tlock(sw, *cmd)
    assert second.returncode == 0 and "cached pass" in second.stderr
    assert count.read_text().count("run") == 1
    assert "out" in open(second.stderr.split("its output: ")[1].split(". Use")[0]).read()
    assert tlock(sw, "--fresh", *cmd).returncode == 0 and count.read_text().count("run") == 2
    # another tree runs again; a failure is not cached
    dev_commit(sw, "X = 5\n")
    (tmp_path / "fail").touch()
    assert tlock(sw, *cmd).returncode == 1 and tlock(sw, *cmd).returncode == 1
    assert count.read_text().count("run") == 4
    # an uncommitted change is never cached
    (tmp_path / "fail").unlink()
    (sw / "dev1" / "src" / "m.py").write_text("X = 6\n")
    assert tlock(sw, *cmd).returncode == 0 and tlock(sw, *cmd).returncode == 0
    assert count.read_text().count("run") == 6
    assert '"cached"' not in (sw / "events.jsonl").read_text()  # the kind stays "test"
    assert "cached head=" in (sw / "events.jsonl").read_text()


def test_tlock_heavy_uses_its_own_slots(tmp_path):
    _, sw = init_swarm(tmp_path, "true")
    # hold the only plain test slot: a heavy run still gets its own
    hold = subprocess.Popen(["flock", str(sw / "test.lock"), "sleep", "30"])
    try:
        __import__("time").sleep(0.3)
        r = tlock(sw, "--heavy", "--fresh", "true")
        assert r.returncode == 0
        assert "start slot=h1" in (sw / "events.jsonl").read_text()
    finally:
        hold.kill()


def add_test_file(repo, sw, name):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text("import m\n\n\ndef test_base():\n    assert m.X\n")
    git("add", ".", cwd=repo)
    git("commit", "-qm", "base tests", cwd=repo)
    git("checkout", "-q", "-B", "swarm-dev1", "swarm-review", cwd=sw / "dev1")


@pytest.mark.parametrize("name,theirs_def,merged", [
    ("tests/test_m.py", "def test_other():", True),    # both only added, in a test file: both kept
    ("src/helpers.py", "def test_other():", False),    # not a test file: back to the dev
    ("tests/test_m.py", "def test_new():", False),     # the same test name added twice: back to the dev
])
def test_a_conflict_where_both_sides_only_added_lines(tmp_path, monkeypatch, name, theirs_def, merged):
    monkeypatch.setattr("time.sleep", lambda s: None)
    repo, sw = init_swarm(tmp_path, "true")
    add_test_file(repo, sw, name)
    base = (repo / name).read_text()
    sha = set_line(sw, name, base + "\n\ndef test_new():\n    assert m.X == 1\n", "[R1-1] new test")
    (repo / name).write_text(base + f"\n\n{theirs_def}\n    assert m.X > 0\n")
    git("commit", "-qam", "[R2-1] other test", cwd=repo)
    board = load_board(sw)
    why = board.merge_one({"id": "R1-1", "sha": sha}, board.cfg())
    if merged:
        assert why is None
        body = git("show", f"HEAD:{name}", cwd=sw / "merge")
        assert "def test_other():" in body and "def test_new():" in body and "<<<<" not in body
        assert body.index("test_other") < body.index("test_new")  # swarm-review's lines first
        assert "the change's own lines are unchanged" in (sw / "events.jsonl").read_text()
    else:
        assert why.startswith("rebase conflict")
