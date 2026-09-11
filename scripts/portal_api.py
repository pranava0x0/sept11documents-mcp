#!/usr/bin/env python3
"""portal_api.py: CLI for the September 11th Document Portal client.

The client itself lives in `sept11/adapters/portal.py` (spec 10: scripts are
thin entrypoints). This file adds argument parsing and stdout formatting, and
re-exports the client so existing imports keep working.

    POST {base}/api/v2/search   query + properties + facets            (JSON)
    POST {base}/api/v2/export   CSV/XLSX/JSON of *every* result of a query
    GET  {front}/apps/content/September11_MD/{BATES}.pdf   the document

Usage:
    python3 scripts/portal_api.py search '"Legislative Alternatives" extension:pdf' --count 5
    python3 scripts/portal_api.py facets --names source agency box_name
    python3 scripts/portal_api.py export --out research/raw/portal-recon/catalog.csv
    python3 scripts/portal_api.py fetch NYC-WTC_000138296 --dest data/pdf
    python3 scripts/portal_api.py --selftest        # offline, no network
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sept11 import config  # noqa: E402
from sept11.adapters.portal import (  # noqa: E402,F401  (re-exported for existing importers)
    BACKEND, CATALOG_PROPERTIES, CONTENT_PATH, FRONT, NoRedirect, PortalClient, PortalError,
    SEARCH_PROPERTIES, USER_AGENT, normalize_bates, parse_catalog_csv, read_bounded,
    selftest, sha256_text, simplify_result, summarize_catalog,
)
from sept11.storage.budget import Budget  # noqa: E402


def _client(base: str) -> PortalClient:
    cfg = config.load()
    return PortalClient(base=base, budget=Budget(cfg.cache_dir / "budget.json", cfg.byte_budget))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="run offline checks and exit")
    ap.add_argument("--base", default=FRONT, help="API base (front door or Mindbreeze backend)")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("--count", type=int, default=5)
    s.add_argument("--pages", type=int, default=1, help="how many pages to walk with paging states")
    f = sub.add_parser("facets"); f.add_argument("--names", nargs="+", default=["source", "agency", "box_name", "production_volume"])
    f.add_argument("--query", default="ALL extension:pdf"); f.add_argument("--max", type=int, default=200)
    e = sub.add_parser("export"); e.add_argument("--out", required=True); e.add_argument("--query", default="ALL extension:pdf")
    d = sub.add_parser("fetch"); d.add_argument("bates", nargs="+"); d.add_argument("--dest", default="data/pdf")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.cmd:
        ap.print_help(); return 2
    client = _client(args.base)
    if args.cmd == "search":
        n = 0
        for r in client.iter_results(args.query, page_size=args.count, max_results=args.count * args.pages):
            n += 1
            print(json.dumps(r, ensure_ascii=False))
        print(f"# {n} results, {client.requests_made} requests", file=sys.stderr)
    elif args.cmd == "facets":
        for name, entries in client.facets(args.names, query=args.query, max_entries=args.max).items():
            print(f"## {name} ({len(entries)} entries)")
            for label, count in entries:
                print(f"{count:>8}  {label}")
    elif args.cmd == "export":
        text = client.export_csv(query=args.query)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        rows = parse_catalog_csv(text)
        print(json.dumps(summarize_catalog(rows), indent=2))
        print(f"# wrote {args.out}: {len(rows)} rows, sha256 {sha256_text(text)[:16]}", file=sys.stderr)
    elif args.cmd == "fetch":
        for b in args.bates:
            p = client.fetch_pdf(b, Path(args.dest))
            print(f"{b} -> {p} ({p.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
