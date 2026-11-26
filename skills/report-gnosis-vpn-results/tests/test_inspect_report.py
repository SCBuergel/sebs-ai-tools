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
    (svg(' class="overview"', 340, 600) + svg("", 900, 340), 0, "fits one phone screen"),
    (svg(' class="overview"', 400, 660), 0, "fits one phone screen"),
    (svg(' class="overview"', 340, 1240), 1, "340 x 1240"),            # the doubled y axis
    (svg(' class="overview"', 900, 340), 1, "900 x 340"),
    (svg("", 900, 340) + svg(' class="overview"', 340, 600), 1, "first block is not the overview"),
    ("<p>no charts</p>", 1, "first block is not the overview"),
    ('<svg data-class="overview" viewBox="0 0 340 600"><text>Mbit/s</text></svg>', 1, "first block is not the overview"),
    ('<svg class="overview"><text>Mbit/s</text></svg>', 1, "has no viewBox"),
])
def test_the_first_chart_must_be_the_overview_and_fit_a_phone(tmp_path, body, rc, says):
    r = run(tmp_path, body)
    assert r.returncode == rc and says in r.stdout
    assert "[svg: Mbit/s]" in r.stdout or "no charts" in r.stdout  # the page is still shown


def test_a_privacy_finding_still_hides_the_page(tmp_path):
    r = run(tmp_path, svg(' class="overview"', 340, 600) + "<p>10.1.2.3</p>")
    assert r.returncode == 1 and "IPv4 address" in r.stdout and "[svg:" not in r.stdout and "10.1.2.3" not in r.stdout
