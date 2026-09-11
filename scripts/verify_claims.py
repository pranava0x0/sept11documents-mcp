#!/usr/bin/env python3
"""verify_claims.py: check that every claim we publish traces to evidence.

A claim is a JSON object (see research/claims/claims.json):

    {"id": "harding-35000",
     "claim": "The Harding memo estimated ~35,000 potential plaintiffs.",
     "quote": "approximately 35,000 potential plaintiffs",
     "variants": ["35,000 potential plaintiffs"],
     "source": {"type": "portal", "bates": "NYC-WTC_000138296", "page": 1}}

    {"id": "settlement-monthly-meetings",
     "claim": "The City must meet monthly with 9/11 Health Watch for 12 months.",
     "quote": "agrees to meet monthly with Petitioner",
     "source": {"type": "url", "url": "https://www.911healthwatch.org/files/101ba5889feaae84c19c8f8.pdf",
                "local_text": "research/raw/sources/2026-09-08-911healthwatch-settlement-agreement.txt"}}

Checks, cheapest first (base-files/DATA.md + the government-data skill):
  1. completeness: id, claim, source present; quote present unless the claim is
     a pure existence claim (then outcome is "no-claim")
  2. portal sources: the Bates number exists in the newest catalog snapshot,
     the page is within page_count, and (if the PDF text was extracted to
     research/raw/sources/portal/<BATES>.txt) the quote appears on that page
  3. url sources: the quote appears in the cached local text; with --online
     the URL is also probed and classified live / blocked / dead

Matching is whitespace- and case-insensitive because several scanned PDFs
extract without spaces. Outcomes are reported separately, never collapsed:
found, not-found, fetch-failed, no-claim, unverifiable (no cached text).
Strict mode accepts only found; text matching does not establish semantic accuracy.
Catalog sources instead use local_csv + sha256 and expected documents/pages.

Usage:
    python3 scripts/verify_claims.py research/claims/claims.json [--online] [--strict] [--md report.md]
    python3 scripts/verify_claims.py --selftest
"""
from __future__ import annotations

import argparse
import json
import hashlib
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sept11.adapters import portal as portal_api  # noqa: E402
from sept11.core.text import PAGE_RE, norm, quote_hits, split_pages  # noqa: E402,F401

DEAD_CODES = {404, 410}
BLOCKED_CODES = {401, 402, 403, 406, 409, 429, 451, 500, 503}


def newest_catalog(dirpath: Path) -> Path | None:
    files = sorted(dirpath.glob("catalog_*_pdf.csv"))
    return files[-1] if files else None


def classify_status(code: int | None, err: str | None) -> str:
    if code is not None:
        if 200 <= code < 300:
            return "live"
        if 300 <= code < 400:
            return "blocked"  # unresolved redirect, not evidence that a source is dead
        if code in DEAD_CODES:
            return "dead"
        if code in BLOCKED_CODES:
            return "blocked"
        return "dead"
    e = (err or "").lower()
    if any(k in e for k in ("timed out", "timeout", "reset", "ssl", "eof")):
        return "blocked"
    return "dead"


def probe(url: str, timeout: float = 20.0) -> tuple[str, int | None, str | None]:
    req = urllib.request.Request(url, headers={"User-Agent": portal_api.USER_AGENT}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return classify_status(r.status, None), r.status, None
    except urllib.error.HTTPError as e:
        return classify_status(e.code, None), e.code, None
    except Exception as e:  # noqa: BLE001 - we want the message, then classify
        return classify_status(None, str(e)), None, str(e)


def verify(claims: list[dict], root: Path, online: bool = False, catalog_rows: list[dict] | None = None,
           log=print) -> list[dict]:
    if catalog_rows is None:
        cat = newest_catalog(root / "research" / "raw" / "portal-recon")
        catalog_rows = portal_api.parse_catalog_csv(cat.read_text(encoding="utf-8")) if cat else []
    by_bates = {r["bates"]: r for r in catalog_rows}
    results = []
    last_host_hit: dict[str, float] = {}
    seen = set()
    for c in claims:
        cid = c.get("id", "?")
        src = c.get("source") or {}
        quote = c.get("quote") or ""
        variants = c.get("variants") or []
        if not c.get("id") or cid in seen:
            results.append({"id": cid, "outcome": "fetch-failed", "detail": "missing or duplicate claim id"})
            continue
        seen.add(cid)
        if not c.get("claim") or not src or not src.get("type"):
            results.append({"id": cid, "outcome": "fetch-failed", "detail": "incomplete record (claim/source missing)"})
            continue
        page = src.get("page")
        if page is not None and (type(page) is not int or page < 1):
            results.append({"id": cid, "outcome": "not-found", "detail": "page must be a positive integer"})
            continue
        if src["type"] == "catalog":
            try:
                path = (root / src["local_csv"]).resolve()
                if not path.is_relative_to(root.resolve()):
                    raise ValueError("catalog path outside project")
                body = path.read_bytes()
                rows = portal_api.parse_catalog_csv(body.decode("utf-8"))
                computed = portal_api.summarize_catalog(rows)
                # Every expected key is recomputed from the pinned bytes; a key the summary
                # does not define is a failure, never an unchecked number.
                expected = c.get("expected") or {}
                actual = {k: computed.get(k, "not computed") for k in expected}
                actual.setdefault("documents", computed["documents"])
                actual.setdefault("pages", computed["pages"])
                valid = (bool(rows) and len({r["bates"] for r in rows}) == len(rows)
                         and hashlib.sha256(body).hexdigest() == src["sha256"]
                         and bool(expected) and all(actual[k] == v for k, v in expected.items()))
                outcome, detail = ("found" if valid else "not-found"), f"computed {actual}; pinned CSV hash and unique IDs checked"
            except (OSError, ValueError, KeyError, TypeError, portal_api.PortalError) as exc:
                outcome, detail = "unverifiable", f"catalog computation failed: {exc}"
            results.append({"id": cid, "outcome": outcome, "detail": detail})
            continue
        if not quote:
            results.append({"id": cid, "outcome": "no-claim", "detail": "no quote or typed computation; not verified"})
            continue
        if src["type"] == "portal":
            bates = src.get("bates", "")
            page = src.get("page")
            row = by_bates.get(bates)
            if catalog_rows and not row:
                results.append({"id": cid, "outcome": "not-found", "detail": f"{bates} not in catalog"})
                continue
            if row and page and row["page_count"] and page > row["page_count"]:
                results.append({"id": cid, "outcome": "not-found", "detail": f"page {page} > page_count {row['page_count']}"})
                continue
            txt = root / "research" / "raw" / "sources" / "portal" / f"{bates}.txt"
            if not txt.exists():
                results.append({"id": cid, "outcome": "unverifiable", "detail": f"no extracted text at {txt.relative_to(root)}"})
                continue
            pages = split_pages(txt.read_text(encoding="utf-8", errors="replace"))
            targets = [page] if page else sorted(pages)
            hit = next((p for p in targets if quote_hits(pages.get(p, ""), quote, variants)), None)
            if hit:
                results.append({"id": cid, "outcome": "found", "detail": f"{bates} p.{hit}"})
            else:
                results.append({"id": cid, "outcome": "not-found", "detail": f"quote absent from {bates} pages {targets[:5]}"})
        elif src["type"] == "url":
            detail = []
            outcome = "unverifiable"
            lt = src.get("local_text")
            if lt:
                p = root / lt
                if p.exists():
                    text = p.read_text(encoding="utf-8", errors="replace")
                    pages = split_pages(text)
                    targets = [page] if page is not None else sorted(pages)
                    pg = next((n for n in targets if quote_hits(pages.get(n, ""), quote, variants)), None)
                    if pg is not None:
                        outcome, d = "found", f"in {lt}" + (f" p.{pg}" if pg else "")
                    else:
                        outcome, d = "not-found", f"quote absent from {lt}"
                    detail.append(d)
                else:
                    detail.append(f"local_text missing: {lt}")
            if online and src.get("url"):
                host = urlparse(src["url"]).hostname or ""
                wait = last_host_hit.get(host, 0) + 1.5 - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                cls, code, err = probe(src["url"])
                last_host_hit[host] = time.monotonic()
                detail.append(f"url {cls} ({code or err})")
                if outcome == "unverifiable" and cls == "live":
                    outcome = "unverifiable"  # live but no cached text to search: still not a content check
                if cls != "live" and outcome == "unverifiable":
                    outcome = "fetch-failed"
            results.append({"id": cid, "outcome": outcome, "detail": "; ".join(detail) or "no local_text and --online not set"})
        else:
            results.append({"id": cid, "outcome": "fetch-failed", "detail": f"unknown source type {src['type']!r}"})
    return results


def render(results: list[dict]) -> str:
    from collections import Counter
    counts = Counter(r["outcome"] for r in results)
    lines = ["# Claim verification", "", "Found means evidence matched or a typed computation passed; semantic review is separate.", "",
             "| outcome | count |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(counts.items())] + ["",
             "| id | outcome | detail |", "|---|---|---|"]
    lines += [f"| {r['id']} | {r['outcome']} | {r['detail']} |" for r in results]
    lines += ["", f"examined {len(results)} claims"]
    return "\n".join(lines)


def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "research/raw/sources/portal").mkdir(parents=True)
        (root / "research/raw/sources/portal/NYC-WTC_000000001.txt").write_text(
            "===== PAGE 1 =====\nApproximately 35,000  potential\nplaintiffs\n\n===== PAGE 2 =====\nnothing here\n")
        (root / "research/raw/sources/settle.txt").write_text("theCity'sLawDepartmentagreestomeetmonthlywithPetitioner")
        cat = [{"bates": "NYC-WTC_000000001", "page_count": 2}]
        claims = [
            {"id": "a", "claim": "x", "quote": "approximately 35,000 potential plaintiffs",
             "source": {"type": "portal", "bates": "NYC-WTC_000000001", "page": 1}},
            {"id": "b", "claim": "x", "quote": "approximately 35,000 potential plaintiffs",
             "source": {"type": "portal", "bates": "NYC-WTC_000000001", "page": 2}},
            {"id": "c", "claim": "x", "quote": "zzz", "source": {"type": "portal", "bates": "NYC-WTC_000000009"}},
            {"id": "d", "claim": "x", "quote": "agrees to meet monthly with Petitioner",
             "source": {"type": "url", "url": "https://example.org", "local_text": "research/raw/sources/settle.txt"}},
            {"id": "e", "claim": "x", "source": {"type": "url", "url": "https://example.org"}},
            {"id": "f", "claim": "x", "quote": "q", "source": {"type": "portal", "bates": "NYC-WTC_000000001", "page": 9}},
        ]
        (root / "pages.txt").write_text("===== PAGE 1 =====\nfirst\n===== PAGE 2 =====\nsecond\n")
        claims.extend([
            {"id": "wrong-url-page", "claim": "x", "quote": "second",
             "source": {"type": "url", "url": "https://example.org", "local_text": "pages.txt", "page": 1}},
            {"id": "zero-page", "claim": "x", "quote": "first",
             "source": {"type": "url", "local_text": "pages.txt", "page": 0}},
            {"id": "missing-text", "claim": "x", "quote": "first",
             "source": {"type": "url", "local_text": "missing.txt"}},
            {"id": "duplicate", "claim": "x", "quote": "first",
             "source": {"type": "url", "local_text": "pages.txt"}},
            {"id": "duplicate", "claim": "x", "quote": "first",
             "source": {"type": "url", "local_text": "pages.txt"}},
        ])
        got = {r["id"]: r["outcome"] for r in verify(claims, root, catalog_rows=cat, log=lambda *_: None)}
        want = {"a": "found", "b": "not-found", "c": "not-found", "d": "found", "e": "no-claim", "f": "not-found"}
        want.update({"wrong-url-page": "not-found", "zero-page": "not-found",
                     "missing-text": "unverifiable", "duplicate": "fetch-failed"})
        # Exercise the CLI release gate, not only matching internals.
        gate_ok = True
        for record in (claims[4], claims[-3]):
            claim_file = root / "strict.json"
            claim_file.write_text(json.dumps([record]))
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()):
                gate_ok &= main([str(claim_file), "--strict"]) == 1
        # Offline CSV fixture; altered count and bytes must fail.
        body = portal_api._FIXTURE_CSV.encode("utf-8")
        (root / "catalog.csv").write_bytes(body)
        rows = portal_api.parse_catalog_csv(body.decode())
        record = {"id": "derived", "claim": "catalog totals",
                  "expected": {"documents": len(rows), "pages": sum(r["page_count"] or 0 for r in rows)},
                  "source": {"type": "catalog", "local_csv": "catalog.csv", "sha256": hashlib.sha256(body).hexdigest()}}
        gate_ok &= verify([record], root, catalog_rows=[])[0]["outcome"] == "found"
        record["expected"]["documents"] += 1
        gate_ok &= verify([record], root, catalog_rows=[])[0]["outcome"] == "not-found"
        record["expected"]["documents"] -= 1
        record["source"]["sha256"] = "0" * 64
        gate_ok &= verify([record], root, catalog_rows=[])[0]["outcome"] == "not-found"
        bad = {k: (got.get(k), want[k]) for k in want if got.get(k) != want[k]}
        ok2 = classify_status(403, None) == "blocked" and classify_status(404, None) == "dead" and classify_status(None, "timed out") == "blocked"
        print(f"verify_claims selftest: {'ok' if not bad and ok2 and gate_ok else 'FAIL ' + str(bad)} ({len(want) - len(bad)}/{len(want)} claims, status classes {'ok' if ok2 else 'FAIL'})")
        return 0 if not bad and ok2 and gate_ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("claims", nargs="?", help="claims JSON file")
    ap.add_argument("--online", action="store_true", help="also probe URLs (rate-limited)")
    ap.add_argument("--strict", action="store_true", help="exit 1 unless every claim has located evidence or a checked computation")
    ap.add_argument("--md", help="write the report here")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.claims:
        ap.print_help(); return 2
    claims = json.loads(Path(args.claims).read_text(encoding="utf-8"))
    if not claims:
        print("verify_claims: zero claims examined; that is not a pass", file=sys.stderr); return 2
    results = verify(claims, ROOT, online=args.online)
    report = render(results)
    print(report)
    if args.md:
        Path(args.md).write_text(report + "\n", encoding="utf-8")
    bad = [r for r in results if r["outcome"] != "found"]
    return 1 if (args.strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
