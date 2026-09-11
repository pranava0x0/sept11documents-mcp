#!/usr/bin/env python3
"""site_checks.py: structural gates for the published page.

Each rule here is a defect that shipped, or nearly shipped, and that no other
check would catch: a table cell with no column label reads as an unlabelled
fragment on a phone, a `1fr` grid column pushes the page sideways between 900
and 1100px, a motion duration over the budget contradicts the design rules.

    python3 scripts/site_checks.py            # check docs/index.html
    python3 scripts/site_checks.py --selftest # prove each rule can still fail
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
# The mutation fixtures need a page that actually has a table; the index no longer does.
PAGE = DOCS / "toolkit.html"
# The site is four pages; a gate that only reads the index proves nothing about the rest.
PAGES = ["index.html", "toolkit.html", "examples.html", "records.html"]
MOTION_CEILING_MS = 300


def _strip(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def check_page(html: str) -> tuple[list[str], dict]:
    problems: list[str] = []
    counts = {"tables": 0, "cells": 0, "folds": 0, "quotes": 0, "durations": 0}

    for table in re.findall(r"<table>.*?</table>", html, re.S):
        counts["tables"] += 1
        headers = [_strip(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)]

        cols = re.search(r"<colgroup>(.*?)</colgroup>", table, re.S)
        if not cols:
            problems.append(f"table '{headers[:1]}' has no <colgroup>; fixed layout needs explicit widths")
        else:
            # Widths ride on classes: the pages' CSP blocks style attributes.
            widths = [int(w) for w in re.findall(r'class="w(\d+)"', cols.group(1))]
            if len(widths) != len(headers):
                problems.append(f"table '{headers[:1]}' has {len(widths)} <col> for {len(headers)} columns")
            elif sum(widths) != 100:
                problems.append(f"table '{headers[:1]}' column widths total {sum(widths)}%, not 100%")

        for row in re.findall(r"<tr>(.*?)</tr>", table[table.find("<tbody>"):], re.S):
            cells = re.findall(r"<td([^>]*)>", row)
            if len(cells) != len(headers):
                problems.append(f"table '{headers[:1]}' row has {len(cells)} cells for {len(headers)} columns")
            for i, attrs in enumerate(cells):
                counts["cells"] += 1
                label = re.search(r'data-label="([^"]*)"', attrs)
                if not label:
                    problems.append(f"table '{headers[:1]}' cell {i} has no data-label; "
                                    "the stacked phone layout renders it unlabelled")
                elif i < len(headers) and label.group(1) != headers[i]:
                    problems.append(f"table '{headers[:1]}' cell {i} is labelled "
                                    f"{label.group(1)!r} but its column is {headers[i]!r}")

    if "<table" in html and html.count('<div class="tw">') < counts["tables"]:
        problems.append("a table is not wrapped in .tw; wide content must scroll inside its own container")

    for summary in re.findall(r"<details class=\"fold\"[^>]*><summary>(.*?)</summary>", html, re.S):
        counts["folds"] += 1
        if len(_strip(summary)) < 16:
            problems.append(f"fold summary {_strip(summary)!r} does not say what it hides")

    # A quote with no citation beside it is a claim the reader cannot check. A chip, a source
    # link or an "Open source" button must sit between the quote and the next quote or the
    # end of its block.
    for match in re.finditer(r"</q>|</blockquote>", html):
        counts["quotes"] += 1
        after = html[match.end():match.end() + 600]
        stop = re.search(r"<q>|<blockquote>|</li>|</dd>|</p>|</td>|</div>|</h[1-6]>", after)
        span = after[:stop.start()] if stop else after
        if not re.search(r'<a class="(?:cite|source-link|btn)" href=', span):
            before = _strip(html[max(0, match.start() - 80):match.start()])
            problems.append(f"quote ending {before[-50:]!r} has no citation link beside it")

    return problems, counts


def check_stylesheet(css: str, used_widths=()) -> list[str]:
    """Rules that live in the shared stylesheet, checked once."""
    problems = []
    for width in sorted(set(used_widths)):
        if f".w{width}{{width:{width}%}}" not in css:
            problems.append(f"a table uses .w{width} but the stylesheet does not define it")
    if not re.search(r"@media\s*\(max-width:767px\)", css):
        problems.append("no stacked-table breakpoint: tables crush their columns on a phone")
    if re.search(r"grid-template-columns:\s*1fr 1fr", css):
        problems.append("grid-template-columns:1fr 1fr overflows when a cell's min-content is wide; "
                        "use minmax(0,1fr)")
    for value, unit in re.findall(r"(?:transition|animation)[^;}]*?(\d*\.?\d+)(ms|s)\b", css):
        ms = float(value) * (1000 if unit == "s" else 1)
        if ms > MOTION_CEILING_MS:
            problems.append(f"motion of {value}{unit} exceeds the {MOTION_CEILING_MS}ms ceiling")
    if re.search(r"\.cite\{[^}]*overflow-wrap:\s*anywhere", css):
        problems.append(".cite with overflow-wrap:anywhere breaks a Bates label mid-word in a narrow column")
    return problems


PAGE_RULES_ADDED = True

BROKEN = [
    ("cell label dropped", lambda h: h.replace(' data-label="Tool"', "", 1), "page"),
    ("cell label drifted", lambda h: h.replace('data-label="Status"', 'data-label="State"', 1), "page"),
    ("colgroup removed", lambda h: re.sub(r"<colgroup>.*?</colgroup>", "", h, count=1, flags=re.S), "page"),
    ("column widths do not total 100", lambda h: h.replace('<col class="w22">', '<col class="w30">', 1), "page"),
    ("width class not defined in the stylesheet",
     lambda c: c.replace(".w22{width:22%}", ".w23{width:23%}", 1), "css"),
    ("grid column trap", lambda c: c.replace("grid-template-columns:minmax(0,1fr) minmax(0,1fr)",
                                             "grid-template-columns:1fr 1fr", 1), "css"),
    ("motion over budget",
     lambda c: c.replace("animation:unfold 180ms ease-out", "animation:unfold 900ms ease-out", 1), "css"),
    ("stacked breakpoint removed",
     lambda c: c.replace("@media (max-width:767px)", "@media (min-width:20000px)", 1), "css"),
    ("citation breaks mid-word", lambda c: c.replace(".cite{white-space:normal;overflow-wrap:break-word}",
                                                     ".cite{white-space:normal;overflow-wrap:anywhere}", 1), "css"),
    ("quote without a citation",
     lambda h: re.sub(r'(</q>)\s*<a class="cite"[^>]*>[^<]*</a>', r"\1", h, count=1), "index"),
    ("blockquote without a citation",
     lambda h: re.sub(r'(</blockquote>)\s*<a class="cite"[^>]*>[^<]*</a>', r"\1", h, count=1), "records"),
]


def selftest() -> int:
    html = PAGE.read_text(encoding="utf-8")
    css = (DOCS / "assets" / "site.css").read_text(encoding="utf-8")
    subjects = {"page": html, "css": css, "index": (DOCS / "index.html").read_text(encoding="utf-8"),
                "records": (DOCS / "records.html").read_text(encoding="utf-8")}
    problems, counts = check_page(html)
    failures = []
    for page in PAGES:
        found, _ = check_page((DOCS / page).read_text(encoding="utf-8"))
        if found:
            failures.append(f"{page} must pass: {found[:2]}")
    used = [int(w) for page in PAGES
            for w in re.findall(r'<col class="w(\d+)"', (DOCS / page).read_text(encoding="utf-8"))]
    if check_stylesheet(css, used):
        failures.append(f"the stylesheet must pass: {check_stylesheet(css, used)[:2]}")
    for name, mutate, subject in BROKEN:
        source = subjects[subject]
        broken = mutate(source)
        if broken == source:
            failures.append(f"mutation {name!r} changed nothing; the fixture no longer matches the {subject}")
            continue
        found = check_stylesheet(broken, used) if subject == "css" else check_page(broken)[0]
        if not found:
            failures.append(f"mutation {name!r} was not caught")
    for message in failures:
        print("FAIL:", message)
    print(f"site_checks selftest: {len(failures)} failures, "
          f"{len(BROKEN) + len(PAGES) + 1 - len(failures)}/{len(BROKEN) + len(PAGES) + 1} checks passed "
          f"over {len(PAGES)} pages and the shared stylesheet")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("page", nargs="?", type=Path, default=PAGE)
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    totals = {"tables": 0, "cells": 0, "folds": 0, "quotes": 0}
    problems, widths = [], []
    for page in PAGES:
        html = (DOCS / page).read_text(encoding="utf-8")
        found, counts = check_page(html)
        problems += [f"{page}: {p}" for p in found]
        widths += [int(w) for w in re.findall(r'<col class="w(\d+)"', html)]
        for key in totals:
            totals[key] += counts[key]
    problems += [f"assets/site.css: {p}" for p in
                 check_stylesheet((DOCS / "assets" / "site.css").read_text(encoding="utf-8"), widths)]
    for problem in problems:
        print("PROBLEM:", problem)
    print(f"examined {len(PAGES)} pages: {totals['tables']} tables, {totals['cells']} cells, "
          f"{totals['folds']} folds, {totals['quotes']} quotes, and the shared stylesheet")
    if totals["tables"] == 0:
        print("site_checks: scanned no tables; that is a broken run, not a pass", file=sys.stderr)
        return 2
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
