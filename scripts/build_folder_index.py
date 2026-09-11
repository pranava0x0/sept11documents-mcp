#!/usr/bin/env python3
"""build_folder_index.py: the archive's folder labels as one small public file.

The catalog has no titles and no dates; what it has is the City's own physical
labels — 4,173 distinct folder names, many of them building addresses with a
BIN and block/lot. That is the germ of the building index (spec 03, T5) and
the thing a survivor asks first: which folders mention my building?

This writes `docs/data/folders.json` from the latest accepted catalog snapshot:
one row per (collection, box, folder) with document and page counts and the
first Bates number in the folder. Labels are transcribed as the City produced
them. Every label passes the PII screen; one that fires is withheld and
counted, never published.

    python3 scripts/build_folder_index.py            # write docs/data/folders.json
    python3 scripts/build_folder_index.py --check    # fail if the file is stale
    python3 scripts/build_folder_index.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sept11 import config  # noqa: E402
from sept11.adapters import portal  # noqa: E402
from sept11.core.pii import screen  # noqa: E402
from sept11.storage.cache import write_atomic  # noqa: E402
from sept11.storage.snapshots import SnapshotStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "docs" / "data" / "folders.json"
WITHHELD = "[label withheld pending review]"


def build_index(rows: list[dict], snapshot_id: str, captured_at: str, csv_sha256: str) -> dict:
    groups: dict = defaultdict(lambda: {"documents": 0, "pages": 0, "first": None})
    for row in rows:
        key = (row.get("source") or "", row.get("box") or "", row.get("folder") or "")
        group = groups[key]
        group["documents"] += 1
        group["pages"] += row.get("page_count") or 0
        if group["first"] is None or row["bates"] < group["first"]:
            group["first"] = row["bates"]
    sources = sorted({key[0] for key in groups})
    withheld = 0
    out = []
    for (source, box, folder), group in sorted(groups.items()):
        label = folder
        if label and screen(label).suspect:
            withheld += 1
            label = WITHHELD
        out.append([sources.index(source), box, label, group["documents"], group["pages"], group["first"]])
    return {
        "generated_from": snapshot_id,
        "captured_at": captured_at,
        "csv_sha256": csv_sha256,
        "documents": len(rows),
        "rows_total": len(out),
        "labels_withheld": withheld,
        "sources": sources,
        "columns": ["source_index", "box", "folder", "documents", "pages", "first_bates"],
        "note": ("Folder and box labels are the City's physical labels, transcribed as produced; an empty "
                 "folder is a document the City filed under a box with no folder label. Counts come from "
                 "the catalog snapshot named above. A label the PII screen flags is withheld."),
        "rows": out,
    }


def render(index: dict) -> bytes:
    # Compact rows, one per line: readable in a diff, small on the wire.
    head = {k: v for k, v in index.items() if k != "rows"}
    body = json.dumps(head, indent=2, ensure_ascii=False)[:-2]  # drop the closing "\n}"
    lines = ",\n".join("    " + json.dumps(r, ensure_ascii=False) for r in index["rows"])
    return (body + ',\n  "rows": [\n' + lines + "\n  ]\n}\n").encode("utf-8")


def build(check: bool) -> int:
    cfg = config.load()
    store = SnapshotStore(cfg.snapshot_dir)
    snapshot = store.latest_accepted()
    if snapshot is None:
        print("build_folder_index: no accepted catalog snapshot; run `python3 scripts/watchdog.py run` first",
              file=sys.stderr)
        return 2
    rows = snapshot.rows(portal.parse_catalog_csv)
    index = build_index(rows, snapshot.snapshot_id, snapshot.captured_at, snapshot.manifest["csv_sha256"])
    payload = render(index)
    current = TARGET.read_bytes() if TARGET.is_file() else b""
    stale = current != payload
    print(f"examined {len(rows)} catalog rows in {snapshot.snapshot_id}: {index['rows_total']} folder rows, "
          f"{index['labels_withheld']} labels withheld, {len(payload):,} bytes; "
          f"{'stale' if stale else 'current'}")
    if check:
        if stale:
            print("build_folder_index: run `python3 scripts/build_folder_index.py`, then record the file "
                  "with scripts/publication.py", file=sys.stderr)
            return 1
        return 0
    if stale:
        write_atomic(TARGET, payload)
        print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


def selftest() -> int:
    failures = []
    rows = [
        {"bates": "NYC-WTC_000000002", "source": "S", "box": "Box 1", "folder": "15 JOHN STREET", "page_count": 3},
        {"bates": "NYC-WTC_000000001", "source": "S", "box": "Box 1", "folder": "15 JOHN STREET", "page_count": 2},
        {"bates": "NYC-WTC_000000003", "source": "S", "box": "Box 1", "folder": "", "page_count": 1},
        {"bates": "NYC-WTC_000000004", "source": "T", "box": "", "folder": "Claimant SSN 123-45-6789", "page_count": 1},
    ]
    index = build_index(rows, "snap", "2026-09-09T00:00:00Z", "0" * 64)
    by_folder = {r[2]: r for r in index["rows"]}
    if by_folder.get("15 JOHN STREET", [None] * 6)[3:] != [2, 5, "NYC-WTC_000000001"]:
        failures.append(f"counts or first Bates wrong: {by_folder.get('15 JOHN STREET')}")
    if "" not in by_folder:
        failures.append("documents without a folder label must still be counted")
    if index["labels_withheld"] != 1 or "123-45-6789" in json.dumps(index):
        failures.append("a label that fires the PII screen must be withheld")
    if index["sources"] != ["S", "T"] or index["rows"][-1][0] != 1:
        failures.append("source indexes must be stable and sorted")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "f.json"
        path.write_bytes(render(index))
        if json.loads(path.read_text())["rows_total"] != 3:
            failures.append("rendered file must parse back to the same rows")
    for message in failures:
        print("FAIL:", message)
    print(f"build_folder_index selftest: {len(failures)} failures, {5 - len(failures)}/5 checks passed "
          f"over {len(rows)} fixture rows")
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
