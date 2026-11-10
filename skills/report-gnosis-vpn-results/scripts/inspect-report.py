#!/usr/bin/env python3
"""Make a large report.html readable, and scan it before it is published.

Usage: inspect-report.py REPORT.html [--scan-only]

Prints the page with every <svg>...</svg> reduced to its <text> labels, so
the content can be read even when the inline charts make the file too large.
Then scans the whole file, charts included, for <script> tags, external URLs,
IPv4 addresses and key-like strings, and prints each finding with its line.
Well-known public resolvers (ping targets such as 9.9.9.9) are listed as
expected and not counted.
Exits 1 when the scan finds anything, 0 when it is clean. Read-only.
"""
import html
import re
import sys

SVG = re.compile(r"<svg\b.*?</svg>", re.S | re.I)
TEXT = re.compile(r"<text\b[^>]*>(.*?)</text>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")
# Namespace URIs inside inline SVG are identifiers, not links.
NAMESPACES = ("http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink")
# Public resolvers used as ping targets are part of the test specification.
PUBLIC_IPS = {"1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "149.112.112.112"}
CHECKS = [
    ("script tag", re.compile(r"<script\b", re.I)),
    ("external URL", re.compile(r"\b(?:https?|wss?|ftp)://[^\s\"'<>)]+", re.I)),
    ("IPv4 address", re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("64-hex secret", re.compile(r"\b(?:0x)?[0-9a-fA-F]{64}\b")),
    ("token prefix", re.compile(r"\b(?:dop_v1_|ghp_|gho_|github_pat_|sk-ant-|xox[bp]-)[A-Za-z0-9_-]{8,}")),
    ("long base64", re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{48,}={0,2}(?![A-Za-z0-9+/=])")),
]


def labels(match: re.Match) -> str:
    found = [html.unescape(TAG.sub("", t)).strip() for t in TEXT.findall(match.group(0))]
    return "[svg: " + " | ".join(t for t in found if t) + "]"


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    page = open(args[0], encoding="utf-8").read()
    if "--scan-only" not in args:
        print(SVG.sub(labels, page))
        print("=" * 72)
    findings = 0
    for number, line in enumerate(page.splitlines(), 1):
        for name, pattern in CHECKS:
            for hit in pattern.findall(line):
                if name == "external URL" and hit.startswith(NAMESPACES):
                    continue
                if name == "IPv4 address" and hit in PUBLIC_IPS:
                    print(f"line {number}: public resolver, expected: {hit}")
                    continue
                findings += 1
                print(f"line {number}: {name}: {hit[:80]}")
    print(f"scan: {findings} finding(s) in {len(page):,} bytes")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
