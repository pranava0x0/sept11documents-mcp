#!/usr/bin/env python3
"""export_catalog.py: snapshot the portal's document catalog (one API call).

Writes two files under research/raw/portal-recon/ (or --out-dir):
    catalog_<YYYY-MM-DD>_pdf.csv       the raw Mindbreeze export (semicolon CSV)
    catalog_<YYYY-MM-DD>_summary.json  counts by source/agency/volume, pages, bytes, sha256

Then, if an earlier catalog exists, prints the diff against the newest one
(delegates to catalog_diff.py). This is the daily "release watchdog" primitive:
the City's own Update History page lists additions at agency level only; this
catalog is document-level.

Usage:
    python3 scripts/export_catalog.py                 # snapshot + diff vs previous
    python3 scripts/export_catalog.py --no-diff
    python3 scripts/export_catalog.py --out-dir data/catalogs
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sept11.adapters import portal as portal_api  # noqa: E402
from catalog_diff import diff_catalogs, render_markdown  # noqa: E402

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "research" / "raw" / "portal-recon"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="label for the files (default: today)")
    ap.add_argument("--no-diff", action="store_true")
    ap.add_argument("--base", default=portal_api.FRONT)
    args = ap.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    previous = sorted(p for p in args.out_dir.glob("catalog_*_pdf.csv") if args.date not in p.name)
    client = portal_api.PortalClient(base=args.base)
    text = client.export_csv()
    rows = portal_api.parse_catalog_csv(text)
    if not rows:
        print("export_catalog: export returned zero rows; refusing to write an empty snapshot", file=sys.stderr)
        return 1
    csv_path = args.out_dir / f"catalog_{args.date}_pdf.csv"
    csv_path.write_text(text, encoding="utf-8")
    # Hash the bytes on disk: a digest of a re-encoding names a different artifact.
    csv_sha256 = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    summary = portal_api.summarize_catalog(rows)
    summary.update({"captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                    "csv_sha256": csv_sha256, "csv_file": csv_path.name,
                    "api_base": args.base, "requests_made": client.requests_made})
    (args.out_dir / f"catalog_{args.date}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

    if not args.no_diff:
        if previous:
            old_rows = portal_api.parse_catalog_csv(previous[-1].read_text(encoding="utf-8"))
            report = diff_catalogs(old_rows, rows)
            print(render_markdown(report, previous[-1].name, csv_path.name))
        else:
            print("# no earlier catalog to diff against (this is the baseline)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
