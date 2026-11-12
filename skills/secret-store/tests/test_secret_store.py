"""Tests for scripts/secret-store. Run: python3 -m pytest <skill>/tests. Uses SECRET_STORE_DIR under tmp_path."""
import hashlib
import os
import stat
import subprocess
import sys

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "secret-store")


def run(tmp_path, *args, stdin=""):
    return subprocess.run([sys.executable, SCRIPT, *args], input=stdin, capture_output=True, text=True,
                          env={**os.environ, "SECRET_STORE_DIR": str(tmp_path / "s")})


def test_stores_privately_and_never_prints_the_value(tmp_path):
    r = run(tmp_path, "api-token", stdin="s3cr3t-value\n")
    assert r.returncode == 0
    assert "s3cr3t-value" not in r.stdout + r.stderr
    assert f"12 characters, sha256 {hashlib.sha256(b's3cr3t-value').hexdigest()[:8]}" in r.stdout
    f = tmp_path / "s" / "api-token"
    assert f.read_text() == "s3cr3t-value"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "s").stat().st_mode) == 0o700
    assert run(tmp_path, "--path", "api-token").stdout.strip() == str(f)
    listing = run(tmp_path, "--list").stdout
    assert "api-token" in listing and "s3cr3t" not in listing
    assert run(tmp_path, "--rm", "api-token").returncode == 0 and not f.exists()


def test_refuses_bad_names_and_empty_secrets(tmp_path):
    assert run(tmp_path, "../x", stdin="v").returncode == 2
    assert run(tmp_path, ".hidden", stdin="v").returncode == 2
    assert run(tmp_path, "ok", stdin="  \n").returncode == 2
    assert not (tmp_path / "s" / "ok").exists()
    assert run(tmp_path, "ok", "extra").returncode == 2
    assert run(tmp_path, "--rm", "missing").returncode == 1


def test_keeps_spaces_and_drops_only_one_line_ending(tmp_path):
    assert run(tmp_path, "p", stdin=" pass word \n").returncode == 0
    assert (tmp_path / "s" / "p").read_text() == " pass word "


def test_clipboard(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for tool in ("wl-paste", "xclip", "xsel"):  # fakes for every tool, so a real one is never used
        (bin_ / tool).write_text("#!/bin/sh\nprintf 'from-clip'\n")
        (bin_ / tool).chmod(0o755)
    env = {**os.environ, "SECRET_STORE_DIR": str(tmp_path / "s"), "PATH": f"{bin_}:/usr/bin:/bin"}
    r = subprocess.run([sys.executable, SCRIPT, "c", "--clipboard"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "s" / "c").read_text() == "from-clip" and "from-clip" not in r.stdout
