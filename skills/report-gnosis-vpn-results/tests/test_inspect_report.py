"""Tests for inspect-report.py. Run: python3 -m pytest <skill>/tests"""
import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "inspect-report.py")


def run(tmp_path, body):
    page = tmp_path / "report.html"
    page.write_text(f"<html><body><h1>Report</h1>{body}</body></html>")
    return subprocess.run([sys.executable, "-I", SCRIPT, str(page)], capture_output=True, text=True)


def svg(cls, w, h):
    return f'<svg xmlns="http://www.w3.org/2000/svg"{cls} viewBox="0 0 {w} {h}"><text>Mbit/s</text></svg>'


@pytest.mark.parametrize("body,rc,says", [
    (svg(' class="overview"', 340, 600) + svg("", 900, 340), 0, "within its size limit"),
    (svg(' class="overview"', 400, 660), 0, "within its size limit"),
    (svg(' class="overview"', 340, 1240), 1, "340 x 1240"),            # the doubled y axis
    (svg(' class="overview"', 900, 340), 1, "900 x 340"),
    (svg("", 900, 340) + svg(' class="overview"', 340, 600), 1, "first block is not the overview"),
    ("<p>no charts</p>", 1, "first block is not the overview"),
    ('<svg data-class="overview" viewBox="0 0 340 600"><text>Mbit/s</text></svg>', 1, "first block is not the overview"),
    ('<svg class="overview"><text>Mbit/s</text></svg>', 1, "has no viewBox"),
    (svg(' class="overview wide"', 680, 600), 0, "within its size limit"),  # four columns
    (svg(' class="wide overview"', 680, 660), 0, "within its size limit"),
    (svg(' class="overview wide"', 700, 600), 1, "at most 680 x 660"),
    (svg(' class="overview wide"', 680, 700), 1, "at most 680 x 660"),
    (svg(' class="overview"', 680, 600), 1, "at most 400 x 660"),          # wide needs the class
    (svg(' class="overviewwide"', 340, 600), 1, "first block is not the overview"),
])
def test_the_first_chart_must_be_the_overview_and_fit_a_phone(tmp_path, body, rc, says):
    r = run(tmp_path, body)
    assert r.returncode == rc and says in r.stdout
    assert "[svg: Mbit/s]" in r.stdout or "no charts" in r.stdout  # the page is still shown


def test_a_privacy_finding_still_hides_the_page(tmp_path):
    r = run(tmp_path, svg(' class="overview"', 340, 600) + "<p>10.1.2.3</p>")
    assert r.returncode == 1 and "IPv4 address" in r.stdout and "[svg:" not in r.stdout and "10.1.2.3" not in r.stdout


COMMIT = "7f48c8e93871db2f917f344c346dc1367e0726cb"
PERMALINK = "https://github.com/SCBuergel/gnosis_vpn-bench/tree/"


@pytest.mark.parametrize("commit", [COMMIT, COMMIT[:7]])
def test_the_bench_permalink_is_expected(tmp_path, commit):
    r = run(tmp_path, svg(' class="overview"', 340, 600) + f'<p><a href="{PERMALINK}{commit}">bench</a></p>')
    assert r.returncode == 0, r.stdout
    assert "bench permalink, expected" in r.stdout and "scan: 0 finding(s)" in r.stdout


@pytest.mark.parametrize("url", [
    PERMALINK + COMMIT + "/evil",                                   # anything after the commit
    PERMALINK + COMMIT[:6],                                         # too short to be a commit
    PERMALINK + COMMIT.upper(),
    "https://github.com/someone/gnosis_vpn-bench/tree/" + COMMIT,   # another owner
    "https://example.com/tree/" + COMMIT,
])
def test_other_urls_still_count(tmp_path, url):
    r = run(tmp_path, svg(' class="overview"', 340, 600) + f'<p><a href="{url}">x</a></p>')
    assert r.returncode == 1 and "external URL" in r.stdout and "[svg:" not in r.stdout


def test_a_permalink_does_not_hide_a_second_url_on_its_line(tmp_path):
    r = run(tmp_path, svg(' class="overview"', 340, 600) + f'<a href="{PERMALINK}{COMMIT}">b</a> https://example.com/x')
    assert r.returncode == 1 and "bench permalink, expected" in r.stdout and "scan: 1 finding(s)" in r.stdout
