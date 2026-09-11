#!/usr/bin/env python3
"""catalog_diff.py: what changed between two portal catalog snapshots.

The settlement promises rolling releases for 12 months and a privilege log of
withheld documents; the portal's Update History only names agencies. This
diff is the document-level record journalists and 9/11 Health Watch can hold
the City to: added, removed, and changed documents (page count, size,
box/folder, production range).

Usage:
    python3 scripts/catalog_diff.py OLD.csv NEW.csv [--json out.json] [--md out.md]
    python3 scripts/catalog_diff.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sept11.adapters import portal as portal_api  # noqa: E402
from sept11.core.catalog import TRACKED, diff_catalogs, render_markdown  # noqa: E402,F401

def selftest() -> int:
    old = [{"bates": "NYC-WTC_000000001", "page_count": 3, "pdf_size": 10, "box": "DEP Box 01", "folder": "A",
            "source": "S", "agency": "DEP", "production_volume": "V1", "production_end": "NYC-WTC_000000003"},
           {"bates": "NYC-WTC_000000009", "page_count": 1, "pdf_size": 5, "box": "DEP Box 01", "folder": "A",
            "source": "S", "agency": "DEP", "production_volume": "V1", "production_end": "NYC-WTC_000000009"}]
    new = [dict(old[0], page_count=4),
           {"bates": "NYC-WTC_000000020", "page_count": 7, "pdf_size": 9, "box": "DEP Box 02", "folder": "B",
            "source": "S", "agency": "DEP", "production_volume": "V2", "production_end": "NYC-WTC_000000026"}]
    r = diff_catalogs(old, new)
    ok = (r["added"] == ["NYC-WTC_000000020"] and r["removed"] == ["NYC-WTC_000000009"]
          and len(r["changed"]) == 1 and r["changed"][0]["changes"] == {"page_count": (3, 4)}
          and r["pages_added"] == 7 and r["pages_removed"] == 1 and r["added_by_box"] == {"DEP Box 02": 1})
    md = render_markdown(r)
    ok2 = "Removed" in md and "NYC-WTC_000000009" in md and "page_count: 3 -> 4" in md
    same = diff_catalogs(new, new)
    ok3 = "No changes." in render_markdown(same)
    print(f"catalog_diff selftest: {'ok' if ok and ok2 and ok3 else 'FAIL'} ({int(ok)+int(ok2)+int(ok3)}/3)")
    return 0 if ok and ok2 and ok3 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old", nargs="?"); ap.add_argument("new", nargs="?")
    ap.add_argument("--json"); ap.add_argument("--md"); ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not (args.old and args.new):
        ap.print_help(); return 2
    old = portal_api.parse_catalog_csv(Path(args.old).read_text(encoding="utf-8"))
    new = portal_api.parse_catalog_csv(Path(args.new).read_text(encoding="utf-8"))
    report = diff_catalogs(old, new)
    md = render_markdown(report, Path(args.old).name, Path(args.new).name)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
