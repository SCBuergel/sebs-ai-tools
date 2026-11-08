"""Tests for swarm-init --py and the merge queue's test run. Run: python3 -m pytest <skill>/tests

Each test builds a throwaway repository and swarm under tmp_path; nothing outside it changes.
"""
import importlib.machinery
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
