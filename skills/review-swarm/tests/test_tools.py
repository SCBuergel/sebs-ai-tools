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


def test_a_non_import_difference_after_a_rebase_goes_back(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    repo, sw = init_swarm(tmp_path, "true")
    board = load_board(sw)
    sha = set_line(sw, "src/m.py", "import os\nX = 1\n", "[R1-1] use os")
    old = git("rev-parse", "swarm-review", cwd=repo)
    assert board.change_without_imports(str(sw / "dev1"), old, sha) == [
        "--- a/src/m.py", "+++ b/src/m.py"]
    sha2 = set_line(sw, "src/m.py", "import os\nX = 9\n", "[R1-1] and more")
    assert board.change_without_imports(str(sw / "dev1"), old, sha2)[2:] == ["-X = 1", "+X = 9"]


def test_any_other_conflict_goes_back(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    repo, sw = init_swarm(tmp_path, "true")
    sha = set_line(sw, "src/m.py", "import os\nX = 2\n", "[R1-1] two")
    advance_review(repo, "import sys\nX = 3\n")
    board = load_board(sw)
    why = board.merge_one({"id": "R1-1", "sha": sha}, board.cfg())
    assert why.startswith("rebase conflict") and "src/m.py" in why


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
