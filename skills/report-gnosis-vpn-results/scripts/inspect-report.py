#!/usr/bin/env python3
"""Scan a large report.html before it is published, then make it readable.

Usage: inspect-report.py REPORT.html [--scan-only]

First scans the whole file, charts included, for <script> tags, external
URLs, IPv4 addresses and key-like strings. Each finding is printed redacted
(kind, line, length, the first 8 hex digits of its SHA-256), never in full,
so a real secret does not end up in a transcript. Well-known public
resolvers (ping targets such as 9.9.9.9) are listed as expected and not
counted, and so is the permalink to the bench commit the report was made
with (https://github.com/SCBuergel/gnosis_vpn-bench/tree/<7-40 hex digits>,
nothing after it); every other external URL is a finding. On any finding it
exits 1 and does not print the page.

It also checks the layout: the page's first chart must be the overview
(`<svg class="overview" ... viewBox="0 0 W H">`) and fit one phone screen,
key included: W <= 400 and H <= 660. The four-column variant the user chose
on 2026-11-27, `<svg class="overview wide" ...>`, may be up to 680 wide.
A layout failure also exits 1, but the
page is still printed, since it is no privacy problem.

When the scan is clean, it prints the page with every <svg>...</svg>
reduced to its <text> labels, so the content can be read even when the
inline charts make the file too large; --scan-only skips that. Read-only.
"""
import hashlib
import html
import re
import sys

SVG = re.compile(r"<svg\b.*?</svg>", re.S | re.I)
TEXT = re.compile(r"<text\b[^>]*>(.*?)</text>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")
# Namespace URIs inside inline SVG are identifiers, not links.
NAMESPACES = ("http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink")
# Public resolvers used as ping targets are part of the test specification.
# The report links the exact bench commit it was made with (user's request of 2026-11-27).
BENCH_PERMALINK = re.compile(r"https://github\.com/SCBuergel/gnosis_vpn-bench/tree/[0-9a-f]{7,40}(?![^\s\"'<>)])")
PUBLIC_IPS = {"1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "149.112.112.112"}
CHECKS = [
    ("script tag", re.compile(r"<script\b", re.I)),
    ("external URL", re.compile(r"\b(?:https?|wss?|ftp)://[^\s\"'<>)]+", re.I)),
    ("IPv4 address", re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("hex secret (64+ digits)", re.compile(r"\b(?:0x)?[0-9a-fA-F]{64,}\b")),
    ("token prefix", re.compile(r"\b(?:dop_v1_|ghp_|gho_|github_pat_|sk-ant-|xox[bp]-)[A-Za-z0-9_-]{8,}")),
    ("long base64", re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{48,}={0,2}(?![A-Za-z0-9+/=])")),
]


# One phone screen in viewBox units, which the overview chart maps to about one CSS pixel each.
PHONE_W, PHONE_H = 400, 660
# The four-column overview ("overview wide") the user chose on 2026-11-27.
WIDE_W = 680
SVG_OPEN = re.compile(r"<svg\b[^>]*>", re.I)
VIEWBOX = re.compile(r"""viewBox\s*=\s*["']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)\s*["']""", re.I)
CLASS = re.compile(r"""(?<![\w-])class\s*=\s*["']([^"']*)["']""", re.I)


def layout_problems(page: str) -> list:
    """Why the first chart does not fit one phone screen, or [] when it does."""
    first = SVG_OPEN.search(page)
    cls = CLASS.search(first.group(0)) if first else None
    classes = cls.group(1).lower().split() if cls else []
    if "overview" not in classes:
        return ["first block is not the overview chart (no <svg class=\"overview\"> before any other chart)"]
    box = VIEWBOX.search(first.group(0))
    if not box:
        return ["the overview chart has no viewBox, so its size on a phone is unknown"]
    w, h = float(box.group(1)), float(box.group(2))
    max_w = WIDE_W if "wide" in classes else PHONE_W
    if w > max_w or h > PHONE_H:
        return [f"the overview chart is {w:g} x {h:g}; it must fit one phone screen, at most {max_w} x {PHONE_H}"]
    return []


def labels(match: re.Match) -> str:
    found = [html.unescape(TAG.sub("", t)).strip() for t in TEXT.findall(match.group(0))]
    return "[svg: " + " | ".join(t for t in found if t) + "]"


def redact(hit: str) -> str:
    digest = hashlib.sha256(hit.encode()).hexdigest()[:8]
    return f"{len(hit)} chars, sha256 {digest}"


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    page = open(args[0], encoding="utf-8").read()
    findings = 0
    for number, line in enumerate(page.splitlines(), 1):
        for link in BENCH_PERMALINK.findall(line):
            print(f"line {number}: bench permalink, expected: {link}")
        # Blank it out so its commit hash is not counted as a long base64 string either.
        line = BENCH_PERMALINK.sub(" ", line)
        for name, pattern in CHECKS:
            for hit in pattern.findall(line):
                if name == "external URL" and hit.startswith(NAMESPACES):
                    continue
                if name == "IPv4 address" and hit in PUBLIC_IPS:
                    print(f"line {number}: public resolver, expected: {hit}")
                    continue
                findings += 1
                print(f"line {number}: {name}: {redact(hit)}")
    print(f"scan: {findings} finding(s) in {len(page):,} bytes")
    if findings:
        print("Not showing the page: tell the user before publishing.")
        return 1
    layout = layout_problems(page)
    for problem in layout:
        print(f"layout: {problem}")
    print(f"layout: {'FAILED, fix the renderer before publishing' if layout else 'first chart fits one phone screen'}")
    if "--scan-only" not in args:
        print("=" * 72)
        print(SVG.sub(labels, page))
    return 1 if layout else 0


if __name__ == "__main__":
    sys.exit(main())
