#!/usr/bin/env python3
"""watchdog.py: the release watchdog (spec 08 §"Watchdog state machine").

One catalog export per run. Each capture is stored in its own immutable
directory, then accepted or quarantined; only accepted snapshots are compared,
and the published pointer moves atomically after the payload is written.

    python3 scripts/watchdog.py run                     # export, store, publish
    python3 scripts/watchdog.py import catalog.csv --captured-at 2026-09-09T15:30:00Z
    python3 scripts/watchdog.py status
    python3 scripts/watchdog.py --selftest              # offline, no network

`export_catalog.py` remains the day-one recon capture; this is the tool meant to
run unattended.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sept11 import ADAPTER_VERSION, config  # noqa: E402
from sept11.adapters import portal  # noqa: E402
from sept11.core.errors import IntegrityError, Sept11Error  # noqa: E402
from sept11.storage.budget import Budget  # noqa: E402
from sept11.storage.cache import sha256_bytes  # noqa: E402
from sept11.storage.publication import Publication  # noqa: E402
from sept11.storage.snapshots import ACCEPTED, SnapshotStore  # noqa: E402

QUERY = "ALL extension:pdf"
PUBLISHED = "docs/data/watchdog/latest.json"
LIST_CAP = 500


def _publish(cfg, store: SnapshotStore, snapshot) -> Path | None:
    """Build the public payload from accepted snapshots and move the pointer atomically."""
    accepted = store.accepted()
    payload = {
        "generated_from": snapshot.snapshot_id,
        "captured_at": snapshot.captured_at,
        "query": snapshot.manifest["query"],
        "documents": snapshot.manifest["documents"],
        "pages": snapshot.manifest["pages"],
        "csv_sha256": snapshot.manifest["csv_sha256"],
        "accepted_snapshots": len(accepted),
        "note": ("Document-level record of what the City's catalog showed at each capture. "
                 "A document missing from a later capture is recorded as observed_absent; "
                 "only the City can say whether it was removed, replaced or withheld."),
    }
    if len(accepted) >= 2:
        report = store.changes_since(accepted[-2].captured_at, portal.parse_catalog_csv)
        payload["interval"] = report["interval"]
        payload["counts"] = {"observed_added": len(report["observed_added"]),
                             "observed_absent": len(report["observed_absent"]),
                             "metadata_changed": len(report["metadata_changed"]),
                             "pages_added": report["pages_added"], "pages_absent": report["pages_absent"]}
        payload["observed_added"] = report["observed_added"][:LIST_CAP]
        payload["observed_absent"] = report["observed_absent"][:LIST_CAP]
        payload["metadata_changed"] = report["metadata_changed"][:LIST_CAP]
        payload["lists_truncated"] = any(len(report[k]) > LIST_CAP for k in
                                         ("observed_added", "observed_absent", "metadata_changed"))
        payload["added_by_box"] = report["added_by_box"]
    else:
        payload["interval"] = None
        payload["note"] += " This is the baseline capture; comparison starts at the next one."

    target = cfg.root / PUBLISHED
    store.publish(target, payload)
    publication = Publication.load(cfg.root)
    publication.record_generated(PUBLISHED, "sept11://watchdog/latest", "daily",
                                 "Watchdog output: catalog-level observations between accepted snapshots.")
    return target


def _report(snapshot) -> int:
    print(json.dumps(snapshot.manifest, indent=2))
    if snapshot.status != ACCEPTED:
        print(f"\nQUARANTINED: {snapshot.manifest['reason']}\n"
              f"The capture is kept at {snapshot.path} for review. The published pointer did not move.",
              file=sys.stderr)
        return 1
    return 0


def cmd_run(cfg, store: SnapshotStore, args) -> int:
    client = portal.PortalClient(base=args.base,
                                 budget=Budget(cfg.cache_dir / "budget.json", cfg.byte_budget))
    text = client.export_csv(query=QUERY)
    snapshot = store.add(text, QUERY, portal.parse_catalog_csv, adapter_version=ADAPTER_VERSION)
    status = _report(snapshot)
    if status == 0:
        target = _publish(cfg, store, snapshot)
        print(f"# published {target}", file=sys.stderr)
    print(f"# examined {snapshot.manifest['documents']} catalog rows in "
          f"{client.requests_made} request(s)", file=sys.stderr)
    return status


def cmd_import(cfg, store: SnapshotStore, args) -> int:
    raw = Path(args.csv).read_bytes()
    text = raw.decode("utf-8")
    # `--captured-at` is a human asserting when bytes were observed. Re-importing the
    # same bytes under a new time invents a capture that never happened; a scheduled
    # run that finds an unchanged catalog is different, and is accepted normally.
    digest = sha256_bytes(raw)
    existing = next((s for s in store.all() if s.manifest["csv_sha256"] == digest), None)
    if existing is not None and not args.force:
        print(f"watchdog: these exact bytes are already stored as {existing.snapshot_id}, captured "
              f"{existing.manifest['captured_at']}. Importing them again under a different time would "
              f"record a capture that did not happen. Pass --force only if the earlier record is wrong.",
              file=sys.stderr)
        return 1
    snapshot = store.add(text, args.query, portal.parse_catalog_csv, captured_at=args.captured_at,
                         adapter_version=args.adapter_version, raw_bytes=raw)
    status = _report(snapshot)
    if status == 0 and not args.no_publish:
        _publish(cfg, store, snapshot)
    print(f"# examined {snapshot.manifest['documents']} catalog rows from {args.csv}; 0 requests",
          file=sys.stderr)
    return status


def cmd_status(cfg, store: SnapshotStore, args) -> int:
    snapshots = store.all()
    for snapshot in snapshots:
        manifest = snapshot.manifest
        print(f"{manifest['captured_at']}  {manifest['status']:<12} {manifest['documents']:>7} docs  "
              f"{manifest['pages']:>8} pages  {snapshot.snapshot_id}"
              + (f"  ({manifest['reason']})" if manifest["reason"] else ""))
    print(f"# {len(snapshots)} snapshots, {len(store.accepted())} accepted, in {store.snapshots_dir}",
          file=sys.stderr)
    return 0 if snapshots else 2


# -- selftest ------------------------------------------------------------------

def _fixture(rows: int, start: int = 1, page_count: int = 2) -> str:
    header = ("﻿Mindbreeze Key;Name;source;agency;box_name;folder_name;page_count;pdf_size;"
              "production_volume;production_end;related_document;Date;Search\n")
    body = "".join(
        f"NYC-WTC_{n:09d};NYC-WTC_{n:09d}.pdf;DEP Hard Copies (68 Boxes);Environmental Protection, Dept. of;"
        f"DEP Box 31;Folder {n};{page_count};{1000 + n};NYC-WTC0003;NYC-WTC_{n:09d};;2026-08-06;\n"
        for n in range(start, start + rows))
    return header + body


def selftest() -> int:
    failures: list[str] = []
    checks = 0

    def check(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(message)

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        store = SnapshotStore(root)
        parse = portal.parse_catalog_csv

        first = store.add(_fixture(100), QUERY, parse, captured_at="2026-09-01T12:00:00+00:00")
        check(first.status == ACCEPTED and first.manifest["documents"] == 100, "baseline accepted")

        # WD-02: a same-day rerun of identical bytes must not overwrite the stored capture.
        try:
            store.add(_fixture(100), QUERY, parse, captured_at="2026-09-01T12:00:00+00:00")
            check(False, "identical same-timestamp capture must be refused, not overwritten")
        except IntegrityError:
            check(True, "immutability")

        second = store.add(_fixture(110), QUERY, parse, captured_at="2026-09-02T12:00:00+00:00")
        check(second.status == ACCEPTED, "growth accepted")

        # WD-01: a truncated export is quarantined, and the accepted history is unchanged.
        truncated = store.add(_fixture(50), QUERY, parse, captured_at="2026-09-03T12:00:00+00:00")
        check(truncated.status == "quarantined" and "fell" in (truncated.manifest["reason"] or ""),
              "large decrease quarantined")
        check(store.latest_accepted().snapshot_id == second.snapshot_id,
              "quarantined capture must not become the latest accepted snapshot")

        # WD-01: duplicate Bates IDs never reach an accepted snapshot.
        duplicated = store.add(_fixture(100) + _fixture(1), QUERY, parse,
                               captured_at="2026-09-04T12:00:00+00:00")
        check(duplicated.status == "quarantined" and "parse_failed" in duplicated.manifest["reason"],
              "duplicate document IDs quarantined")

        # A small decrease is accepted, with the fall recorded as a warning rather than hidden.
        small = store.add(_fixture(106), QUERY, parse, captured_at="2026-09-05T12:00:00+00:00")
        check(small.status == ACCEPTED and any("fell" in w for w in small.manifest["warnings"]),
              "small decrease accepted with a warning")

        # A different query scope is never silently compared against the previous one.
        rescoped = store.add(_fixture(120), "source:\"WTC 7\"", parse,
                             captured_at="2026-09-06T12:00:00+00:00")
        check(rescoped.status == "quarantined", "query scope change quarantined")

        report = store.changes_since("2026-09-01", parse)
        check(report["interval"]["from"] == "2026-09-01T12:00:00+00:00", "baseline at or before honoured")
        check(len(report["observed_added"]) == 6 and report["observed_absent"] == [],
              f"observed 6 additions, got {len(report['observed_added'])}")

        try:
            store.changes_since("2026-08-01", parse)
            check(False, "a request before every snapshot must fail, not silently use the oldest")
        except IntegrityError:
            check(True, "missing baseline is explicit")

        # WD-02: the published pointer survives a failed publish attempt.
        target = root / "latest.json"
        store.publish(target, {"a": 1})
        try:
            store.publish(target, {"bad": {1, 2}})  # a set is not JSON-serializable
        except TypeError:
            pass
        check(json.loads(target.read_text()) == {"a": 1}, "failed publish must leave the last good file")

        # A capture whose bytes are already stored is a re-import, not a new observation.
        check(any(s.manifest["csv_sha256"] == first.manifest["csv_sha256"] for s in store.all()),
              "fixture digest present")

        # Locks are atomic: the second holder is refused rather than sharing the run.
        with store.lock():
            try:
                with store.lock():
                    check(False, "a second run lock must be refused")
            except IntegrityError:
                check(True, "run lock")

    for message in failures:
        print("FAIL:", message)
    print(f"watchdog selftest: {len(failures)} failures, {checks - len(failures)}/{checks} checks passed "
          f"over 7 fixture snapshots; network calls: 0")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--snapshot-dir", type=Path, default=None)
    sub = ap.add_subparsers(dest="cmd")
    # The export lives on the Mindbreeze tenant behind the City's front door: on 2026-09-11 the
    # front door answered 404 for /api/v2/export while /api/v2/search still worked (issues.md).
    # Citations always name the front door regardless of where the catalog was fetched.
    run = sub.add_parser("run"); run.add_argument("--base", default=portal.BACKEND)
    imp = sub.add_parser("import"); imp.add_argument("csv")
    imp.add_argument("--captured-at", required=True, help="ISO-8601 time the bytes were captured")
    imp.add_argument("--query", default=QUERY)
    imp.add_argument("--adapter-version", default="manual-capture")
    imp.add_argument("--no-publish", action="store_true")
    imp.add_argument("--force", action="store_true", help="store bytes already recorded under another time")
    sub.add_parser("status")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.cmd:
        ap.print_help(); return 2
    cfg = config.load()
    store = SnapshotStore(args.snapshot_dir or cfg.snapshot_dir)
    try:
        if args.cmd == "status":
            return cmd_status(cfg, store, args)
        with store.lock():
            return {"run": cmd_run, "import": cmd_import}[args.cmd](cfg, store, args)
    except Sept11Error as exc:
        print(f"watchdog: {exc.code}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
