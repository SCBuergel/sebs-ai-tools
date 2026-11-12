"""Tests for scripts/deadline. Run: python3 -m pytest <skill>/tests. State goes under tmp_path."""
import os
import shutil
import subprocess
import sys
import time

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "deadline")


def run(tmp_path, *args, systemd=False):
    env = {**os.environ, "DEADLINE_STATE_DIR": str(tmp_path / "st"), "DEADLINE_RETRY_S": "0.2"}
    if not systemd:
        env["DEADLINE_NO_SYSTEMD"] = "1"
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, env=env)


def wait_for(cond, s=8):
    end = time.time() + s
    while time.time() < end and not cond():
        time.sleep(0.1)
    return cond()


def test_fires_once_and_clears_itself(tmp_path):
    mark = tmp_path / "deleted"
    r = run(tmp_path, "start", "m1", "1", "--", "sh", "-c", f"echo x >> {mark}")
    assert r.returncode == 0 and "detached process" in r.stdout
    assert "m1" in run(tmp_path, "list").stdout
    assert wait_for(lambda: mark.exists() and not (tmp_path / "st" / "m1.json").exists())
    assert mark.read_text() == "x\n"
    assert run(tmp_path, "start", "m1", "5", "--", "true").returncode == 0  # the name is free again
    run(tmp_path, "cancel", "m1")


def test_retries_a_failing_delete_and_stays_listed(tmp_path):
    count = tmp_path / "tries"
    assert run(tmp_path, "start", "m2", "0", "--", "sh", "-c", f"echo t >> {count}; exit 1").returncode == 0
    assert wait_for(lambda: count.exists() and count.read_text().count("t") == 5)
    time.sleep(0.5)
    assert count.read_text().count("t") == 5
    assert "overdue" in run(tmp_path, "list").stdout
    assert "attempt 5" in (tmp_path / "st" / "m2.fired.log").read_text()


def test_cancel_disarms_and_refuses_unknown_or_double_names(tmp_path):
    mark = tmp_path / "deleted"
    assert run(tmp_path, "start", "m3", "2", "--", "touch", str(mark)).returncode == 0
    assert run(tmp_path, "start", "m3", "2", "--", "true").returncode == 2  # already armed
    assert run(tmp_path, "cancel", "m3").returncode == 0
    time.sleep(2.5)
    assert not mark.exists()
    assert run(tmp_path, "cancel", "m3").returncode == 1
    assert run(tmp_path, "start", "../x", "2", "--", "true").returncode == 2


@pytest.mark.skipif(not shutil.which("systemd-run") or
                    subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True).returncode not in (0, 1),
                    reason="no systemd user manager")
def test_with_systemd_it_is_a_user_timer(tmp_path):
    mark = tmp_path / "deleted"
    name = f"t{os.getpid()}"
    r = run(tmp_path, "start", name, "1", "--", "touch", str(mark), systemd=True)
    assert r.returncode == 0 and f"deadline-{name}.timer" in r.stdout
    assert wait_for(lambda: mark.exists() and not (tmp_path / "st" / f"{name}.json").exists(), 15)
