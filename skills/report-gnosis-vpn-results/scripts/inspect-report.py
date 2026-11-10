#!/usr/bin/env python3
"""Scan a large report.html before it is published, then make it readable.

Usage: inspect-report.py REPORT.html [--scan-only]

First scans the whole file, charts included, for <script> tags, external
URLs, IPv4 addresses and key-like strings. Each finding is printed redacted
(kind, line, length, the first 8 hex digits of its SHA-256), never in full,
so a real secret does not end up in a transcript. Well-known public
resolvers (ping targets such as 9.9.9.9) are listed as expected and not
counted. On any finding it exits 1 and does not print the page.

When the scan is clean (exit 0), it prints the page with every <svg>...</svg>
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
    if "--scan-only" not in args:
        print("=" * 72)
        print(SVG.sub(labels, page))
    return 0


if __name__ == "__main__":
    sys.exit(main())
