#!/usr/bin/env python3
"""record_mcp_examples.py: record what the MCP server actually returns, for the site.

The site shows what a tool call returns, and the only honest way to show that
is to record a real call against this machine's captures and publish the
envelope unchanged. This script runs a fixed list of calls in-process (no
network: live search stays disabled) and writes `docs/data/mcp-examples.json`.

Two things are altered on the way out, and each alteration is named in the
record's `redactions` list: page text is replaced by a note with its length,
because page text is not re-hosted on a public surface without review; and
nothing else. A structured error is recorded as an error, because a refusal
is part of what the server does.

    python3 scripts/record_mcp_examples.py            # write docs/data/mcp-examples.json
    python3 scripts/record_mcp_examples.py --check    # fail if the recorded shapes are stale
    python3 scripts/record_mcp_examples.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sept11 import SCHEMA_VERSION, __version__, config  # noqa: E402
from sept11.core.evidence import envelope_schema  # noqa: E402
from sept11.mcp.context import Context  # noqa: E402
from sept11.mcp.server import PROTOCOL_VERSION, Server  # noqa: E402
from sept11.storage.cache import write_atomic  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "docs" / "data" / "mcp-examples.json"

# The demo each example belongs to, the question it answers, and the call. Order is the
# order the site shows them.
EXAMPLES = [
    {"id": "catalog_search", "title": "Folders in the archive that mention John Street", "demo": "records", "question": "Which folders in the archive mention John Street?",
     "request": {"name": "catalog_search", "arguments": {"folder": "JOHN STREET", "count": 5}}},
    {"id": "portal_browse", "title": "Contents of DEP Box 31", "demo": "records", "question": "What is inside DEP Box 31?",
     "request": {"name": "portal_browse", "arguments": {"source": "DEP Hard Copies (68 Boxes)", "box": "DEP Box 31"}}},
    {"id": "portal_get_document", "title": "The catalog record for the Harding memo", "demo": "records", "question": "What does the catalog hold about the Harding memo?",
     "request": {"name": "portal_get_document", "arguments": {"bates": "NYC-WTC_000138296"}}},
    {"id": "portal_get_page_text", "title": "Page 1 of the Harding memo", "demo": "records", "question": "What is printed on page 1 of the Harding memo?",
     "request": {"name": "portal_get_page_text", "arguments": {"bates": "NYC-WTC_000138296", "page": 1}}},
    {"id": "citations_verify", "title": "The Harding estimate checked on pages 1 and 2", "demo": "records",
     "question": "Is the Harding estimate really on page 1, and is the same quote on page 2?",
     "request": {"name": "citations_verify", "arguments": {"claims": [
         {"id": "harding-p1", "claim": "The memo estimates 35,000 potential plaintiffs.",
          "quote": "approximately 35,000 potential plaintiffs",
          "source": {"type": "portal", "bates": "NYC-WTC_000138296", "page": 1}},
         {"id": "harding-p2", "claim": "The same estimate appears on page 2.",
          "quote": "approximately 35,000 potential plaintiffs",
          "source": {"type": "portal", "bates": "NYC-WTC_000138296", "page": 2}}]}}},
    {"id": "portal_search", "title": "A live portal search with live access off", "demo": "records", "question": "Can the server search the live portal?",
     "request": {"name": "portal_search", "arguments": {"query": '"Clean Up Initiative"', "count": 3}}},
    {"id": "budget_lookup", "title": "Funding for the DOI investigation", "demo": "budget", "question": "What is known about the DOI investigation's funding?",
     "request": {"name": "budget_lookup", "arguments": {"topic": "doi"}}},
    {"id": "doi_milestones", "title": "Upcoming dated obligations", "demo": "budget", "question": "Which dated obligations are upcoming?",
     "request": {"name": "doi_milestones", "arguments": {}}},
    {"id": "portal_catalog_stats", "title": "Size of the captured catalog", "demo": "releases", "question": "How large is the captured catalog?",
     "request": {"name": "portal_catalog_stats", "arguments": {}}},
    {"id": "portal_changes_since", "title": "Catalog changes since September 9, 2026", "demo": "releases", "question": "What changed since September 9, 2026?",
     "request": {"name": "portal_changes_since", "arguments": {"since": "2026-09-09"}}},
    {"id": "building_lookup", "title": "Records filed under 15 John Street", "demo": "address", "question": "What does the archive file under 15 John Street?",
     "request": {"name": "building_lookup", "arguments": {"address": "15 John Street"}}},
    {"id": "building_lookup-chambers", "title": "Records filed under 345 Chambers Street (Stuyvesant High School)", "demo": "address",
     "question": "What does the archive file under 345 Chambers Street (Stuyvesant High School)?",
     "request": {"name": "building_lookup", "arguments": {"address": "345 Chambers Street"}}},
    {"id": "building_lookup-broadway", "title": "Records filed under 120 Broadway", "demo": "address", "question": "What does the archive file under 120 Broadway?",
     "request": {"name": "building_lookup", "arguments": {"address": "120 Broadway"}}},
    {"id": "timeline_lookup", "title": "Public statements and City records in date order", "demo": "timeline",
     "question": "What did officials say in public, and what do the City's records show, in date order?",
     "request": {"name": "timeline_lookup", "arguments": {}}},
    {"id": "readings_lookup", "title": "Sampling results located so far", "demo": "readings", "question": "Which sampling results have been located so far?",
     "request": {"name": "readings_lookup", "arguments": {"include_unreviewed": True}}},
    {"id": "readings_lookup-default", "title": "Asbestos results without unreviewed rows", "demo": "readings",
     "question": "What happens when unreviewed results are not requested?",
     "request": {"name": "readings_lookup", "arguments": {"analyte": "asbestos"}}},
    {"id": "presence_evidence", "title": "Proof of presence the VCF accepts from survivors", "demo": "help",
     "question": "Which documents does the VCF accept from survivors as proof of presence?",
     "request": {"name": "presence_evidence", "arguments": {"program": "vcf", "who": "survivors"}}},
    {"id": "upcoming_dates", "title": "Obligations ahead as of October 8, 2026", "demo": "dates", "question": "Which dated obligations are ahead as of October 8, 2026?",
     "request": {"name": "upcoming_dates", "arguments": {"as_of": "2026-10-08"}}},
    {"id": "portal_query_draft", "title": "A portal search for the Stuyvesant memo", "demo": "search", "question": "How should a search for the Stuyvesant memo be written?",
     "request": {"name": "portal_query_draft", "arguments": {"phrase": "Re-Occupying Stuyvesant High School",
                                                             "box": "DEP Box 37"}}},
    {"id": "citations_format", "title": "Citations for page 1 of the Harding memo", "demo": "cite", "question": "How is page 1 of the Harding memo cited?",
     "request": {"name": "citations_format", "arguments": {"bates": "NYC-WTC_000138296", "page": 1}}},
    {"id": "citations_format-fuchs", "title": "Citations for page 2 of the Fuchs memo", "demo": "cite", "question": "How is page 2 of the Fuchs memo cited?",
     "request": {"name": "citations_format", "arguments": {"bates": "NYC-WTC_000147320", "page": 2}}},
    {"id": "citations_format-15-john", "title": "Citations for the 15 John Street lab report", "demo": "cite", "question": "How is the 15 John Street lab report cited?",
     "request": {"name": "citations_format", "arguments": {"bates": "NYC-WTC_000094256", "page": 1}}},
    {"id": "records_manifest", "title": "Documents behind the research examples", "demo": "manifest", "question": "What are the documents behind the research examples?",
     "request": {"name": "records_manifest", "arguments": {"bates": ["NYC-WTC_000138296", "NYC-WTC_000147320", "NYC-WTC_000153130", "NYC-WTC_000130438", "NYC-WTC_000154488", "NYC-WTC_000094256", "NYC-WTC_000144518", "NYC-WTC_000106411", "NYC-WTC_000148621", "NYC-WTC_000151604"]}}},
]

PAGE_TEXT_NOTE = ("[page text elided from this published example: {n} characters. The tool returns it "
                  "verbatim; read it with portal_get_page_text or open the PDF at the citation.]")


def record_one(server: Server, example: dict) -> dict:
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": example["request"]})
    result = response["result"]
    payload = json.loads(json.dumps(result["structuredContent"]))
    redactions = []
    if not result["isError"] and example["request"]["name"] == "portal_get_page_text":
        text = payload["data"].get("text")
        if text:
            payload["data"]["text"] = PAGE_TEXT_NOTE.format(n=len(text))
            redactions.append("data.text replaced by a note with its length; page text is not re-hosted "
                              "on a public surface without review")
    return {"id": example["id"], "demo": example["demo"], "title": example["title"], "question": example["question"],
            "request": example["request"], "is_error": bool(result["isError"]),
            "response": payload, "redactions": redactions}


def record_all(server: Server, examples: list[dict]) -> dict:
    records = [record_one(server, e) for e in examples]
    return {
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "server_version": __version__, "protocol_version": PROTOCOL_VERSION, "schema_version": SCHEMA_VERSION,
        "note": ("Real calls to the local server, recorded in-process against this machine's captures with "
                 "live search disabled. Envelopes are published unchanged except where `redactions` says "
                 "otherwise. `retrieved_at` on a cached envelope is the capture time, as the warning says."),
        "examples": records,
    }


def _validate(instance, schema, path="envelope") -> list[str]:
    from mcp_checks import validate
    return validate(instance, schema, path)


def build(check: bool) -> int:
    cfg = config.load()
    if cfg.allow_live_search:
        print("record_mcp_examples: refusing to record with SEPT11_ALLOW_LIVE=1; examples must not reach the City",
              file=sys.stderr)
        return 2
    server = Server(ctx=Context(cfg=cfg, log=lambda message: None))
    recorded = record_all(server, EXAMPLES)
    problems = []
    for example in recorded["examples"]:
        if not example["is_error"]:
            problems += _validate(example["response"], envelope_schema(), example["id"])
    if problems:
        for problem in problems:
            print("PROBLEM:", problem, file=sys.stderr)
        return 1
    errors = sum(1 for e in recorded["examples"] if e["is_error"])
    print(f"examined {len(recorded['examples'])} calls across {len({e['request']['name'] for e in EXAMPLES})} "
          f"tools: {errors} structured errors, {sum(len(e['redactions']) for e in recorded['examples'])} redactions; "
          f"network calls: {server.ctx.client().requests_made}")
    if check:
        if not TARGET.is_file():
            print("record_mcp_examples: docs/data/mcp-examples.json is missing", file=sys.stderr)
            return 1
        current = json.loads(TARGET.read_text(encoding="utf-8"))
        # Timestamps and cursors differ between runs; the shapes, ids and outcomes must not.
        def shape(doc):
            return [(e["id"], e["is_error"], sorted((e["response"].get("data") or {}).keys())
                     if isinstance(e["response"].get("data"), dict) else None,
                     [w["code"] for w in e["response"].get("warnings", [])]) for e in doc["examples"]]
        if shape(current) != shape(recorded) or current.get("schema_version") != SCHEMA_VERSION:
            print("record_mcp_examples: the recorded examples are stale; run `python3 scripts/record_mcp_examples.py` "
                  "and record the file with scripts/publication.py", file=sys.stderr)
            return 1
        return 0
    write_atomic(TARGET, (json.dumps(recorded, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


def selftest() -> int:
    from mcp_checks import build_fixture
    failures = []
    with tempfile.TemporaryDirectory() as folder:
        ctx = build_fixture(Path(folder))
        server = Server(ctx=ctx)
        examples = [
            {"id": "page", "title": "t", "demo": "records", "question": "q",
             "request": {"name": "portal_get_page_text", "arguments": {"bates": "NYC-WTC_000000001", "page": 1}}},
            {"id": "refused", "title": "t", "demo": "records", "question": "q",
             "request": {"name": "portal_search", "arguments": {"query": "asbestos"}}},
            {"id": "search", "title": "t", "demo": "records", "question": "q",
             "request": {"name": "catalog_search", "arguments": {"text": "Folder A", "count": 2}}},
        ]
        doc = record_all(server, examples)
        page = doc["examples"][0]
        if "Asbestos results" in json.dumps(page) or "elided" not in page["response"]["data"]["text"]:
            failures.append("page text must be elided and the elision recorded")
        if page["redactions"] == [] or page["response"]["data"]["citation"]["stamp"] != "NYC-WTC_000000001":
            failures.append("the citation must survive the elision and the redaction must be named")
        refused = doc["examples"][1]
        if not refused["is_error"] or refused["response"]["error"]["code"] != "refused":
            failures.append("a refusal must be recorded as a structured error")
        if ctx.client().requests_made != 0:
            failures.append("recording must not reach the network")
        search = doc["examples"][2]
        if _validate(search["response"], envelope_schema(), "search"):
            failures.append("a recorded success must validate against the wire schema")
    for message in failures:
        print("FAIL:", message)
    print(f"record_mcp_examples selftest: {len(failures)} failures, {5 - len(failures)}/5 checks passed "
          "over 3 fixture calls; network calls: 0")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    return build(args.check)


if __name__ == "__main__":
    sys.exit(main())
