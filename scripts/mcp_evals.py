#!/usr/bin/env python3
"""mcp_evals.py: golden evals for the MCP server (spec 02 §5, spec 05 §3.5).

The golden set is `evals/golden.json`, seeded from the reviewed anchor documents
in `review/anchors.json`: for each located document, the Bates number, the page,
the quote a person located there, and the Bates stamp printed on that page.

Offline (default): every anchor must be in the accepted catalog snapshot,
`citations_verify` must locate its quote, and `portal_get_page_text` must read
the recorded stamp off the page. Live (`--live`, opt-in, at most 20 requests,
needs SEPT11_ALLOW_LIVE=1): `portal_search` with the anchor's phrase must return
its Bates number in the top five.

    python3 scripts/mcp_evals.py            # offline, against local captures
    python3 scripts/mcp_evals.py --seed     # rewrite evals/golden.json from review/anchors.json
    python3 scripts/mcp_evals.py --live     # also search the live portal (opt-in)
    python3 scripts/mcp_evals.py --selftest

A failing anchor blocks a release. Zero anchors examined exits 2, not 0.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sept11 import config  # noqa: E402
from sept11.core.citations import stamps_on  # noqa: E402
from sept11.mcp.server import Server  # noqa: E402
from sept11.mcp.context import Context  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ANCHORS = ROOT / "review" / "anchors.json"
MAX_LIVE_REQUESTS = 20


def seed(anchors: dict) -> dict:
    rows = []
    for row in anchors["anchors"]:
        if row.get("status") != "quote_verified":
            continue
        # A recorded stamp is the printed footer ("NYC-WTC 000138296"); the server reports
        # stamps in canonical form, so the golden value is normalized the same way.
        printed = stamps_on(row.get("stamp") or "")
        rows.append({"id": row["id"], "bates": row["bates"], "page": row["page"], "quote": row["quote"],
                     "stamp": printed[0] if printed else None,
                     "search": row.get("search")})
    return {"generated_from": "review/anchors.json", "anchors_seen": len(anchors["anchors"]),
            "note": ("Golden anchors: each quote was located by a person and by verify_claims.py. "
                     "Regenerate with `scripts/mcp_evals.py --seed`; a drifted quote fails the drift check."),
            "rows": rows}


def call(server: Server, name: str, arguments: dict) -> dict:
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": name, "arguments": arguments}})
    return response["result"]


def run(golden: dict, anchors: dict, server: Server, live: bool = False, log=print) -> tuple[int, list[str]]:
    """Return (examined, failures)."""
    failures: list[str] = []
    by_id = {a["id"]: a for a in anchors["anchors"]}
    examined = 0
    live_requests = 0
    for row in golden["rows"]:
        anchor = by_id.get(row["id"])
        if anchor is None or anchor.get("quote") != row["quote"] or anchor.get("page") != row["page"]:
            failures.append(f"{row['id']}: golden row drifted from review/anchors.json; run --seed")
            continue
        examined += 1
        doc = call(server, "portal_get_document", {"bates": row["bates"]})
        if doc["isError"]:
            failures.append(f"{row['id']}: {row['bates']} not in the catalog snapshot: "
                            f"{doc['structuredContent']['error']['message']}")
        verified = call(server, "citations_verify", {"claims": [
            {"id": row["id"], "claim": row["id"], "quote": row["quote"],
             "source": {"type": "portal", "bates": row["bates"], "page": row["page"]}}]})
        outcome = None if verified["isError"] else verified["structuredContent"]["data"]["results"][0]
        if verified["isError"] or outcome["match"] != "found":
            failures.append(f"{row['id']}: quote not located at {row['bates']} p.{row['page']} "
                            f"({'error' if verified['isError'] else outcome['match']}: "
                            f"{(outcome or {}).get('detail') or verified['structuredContent']})")
        page = call(server, "portal_get_page_text", {"bates": row["bates"], "page": row["page"]})
        if page["isError"]:
            failures.append(f"{row['id']}: page text unavailable: {page['structuredContent']['error']['message']}")
        elif row.get("stamp"):
            citation = page["structuredContent"]["data"]["citation"]
            if citation["stamp"] != row["stamp"]:
                failures.append(f"{row['id']}: stamp read {citation['stamp']!r} ({citation['stamp_status']}), "
                                f"golden says {row['stamp']!r}")
        if live and row.get("search") and live_requests < MAX_LIVE_REQUESTS:
            live_requests += 1
            found = call(server, "portal_search", {"query": row["search"], "count": 5})
            hits = [] if found["isError"] else [h["bates"] for h in found["structuredContent"]["data"]["hits"]]
            if row["bates"] not in hits:
                failures.append(f"{row['id']}: live search {row['search']!r} did not return {row['bates']} "
                                f"in the top 5 (got {hits})")
    log(f"examined {examined} golden anchors ({live_requests} live requests): {len(failures)} failures")
    return examined, failures


def selftest() -> int:
    from mcp_checks import build_fixture  # the shared serving fixture: captures, snapshot, allowlist
    failures = []
    with tempfile.TemporaryDirectory() as folder:
        ctx = build_fixture(Path(folder))
        server = Server(ctx=ctx)
        anchors = {"anchors": [
            {"id": "good", "bates": "NYC-WTC_000000001", "page": 1, "quote": "Asbestos results",
             "stamp": "NYC-WTC 000000001", "status": "quote_verified"},
            {"id": "skipped", "bates": "NYC-WTC_000000001", "page": 1, "quote": "x", "status": "candidate"},
        ]}
        golden = seed(anchors)
        if [r["id"] for r in golden["rows"]] != ["good"] or golden["rows"][0]["stamp"] != "NYC-WTC_000000001":
            failures.append(f"seed must keep verified anchors only and normalize stamps: {golden['rows']}")
        examined, found = run(golden, anchors, server, log=lambda *_: None)
        if examined != 1 or found:
            failures.append(f"a good anchor must pass: {found}")
        bad = json.loads(json.dumps(golden))
        bad["rows"][0]["quote"] = "words that are not on the page"
        anchors_bad = json.loads(json.dumps(anchors))
        anchors_bad["anchors"][0]["quote"] = bad["rows"][0]["quote"]
        _, found = run(bad, anchors_bad, server, log=lambda *_: None)
        if not any("not located" in f for f in found):
            failures.append("a missing quote must fail the eval")
        drift = json.loads(json.dumps(golden))
        drift["rows"][0]["quote"] = "edited after seeding"
        _, found = run(drift, anchors, server, log=lambda *_: None)
        if not any("drifted" in f for f in found):
            failures.append("a golden row that no longer matches the anchor must fail")
        stamp = json.loads(json.dumps(golden))
        stamp["rows"][0]["stamp"] = "NYC-WTC_000000009"
        _, found = run(stamp, anchors, server, log=lambda *_: None)
        if not any("stamp read" in f for f in found):
            failures.append("a stamp mismatch must fail the eval")
    for message in failures:
        print("FAIL:", message)
    print(f"mcp_evals selftest: {len(failures)} failures, {4 - len(failures)}/4 checks passed "
          "over 1 fixture anchor; network calls: 0")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", action="store_true", help="rewrite evals/golden.json from review/anchors.json")
    ap.add_argument("--live", action="store_true", help="also run the live search evals (opt-in)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    cfg = config.load()
    anchors = json.loads(ANCHORS.read_text(encoding="utf-8"))
    if args.seed:
        cfg.evals_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.evals_file.write_text(json.dumps(seed(anchors), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {cfg.evals_file.relative_to(ROOT)} with {len(seed(anchors)['rows'])} anchors")
        return 0
    if not cfg.evals_file.is_file():
        print("mcp_evals: no evals/golden.json; run --seed first", file=sys.stderr)
        return 2
    golden = json.loads(cfg.evals_file.read_text(encoding="utf-8"))
    server = Server(ctx=Context(cfg=cfg, log=lambda message: print(message, file=sys.stderr)))
    if args.live and not cfg.allow_live_search:
        print("mcp_evals: --live needs SEPT11_ALLOW_LIVE=1; the queries reach the City", file=sys.stderr)
        return 2
    examined, failures = run(golden, anchors, server, live=args.live)
    for failure in failures:
        print("FAIL:", failure)
    if examined == 0:
        print("mcp_evals: examined nothing; that is a broken run, not a pass", file=sys.stderr)
        return 2
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
