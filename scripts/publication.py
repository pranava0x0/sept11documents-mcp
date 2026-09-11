#!/usr/bin/env python3
"""publication.py: maintain the publication allowlist (`review/publication.json`).

Public surfaces serve the files named in the allowlist and nothing else. This
CLI records a file a maintainer has generated, with its current hash and a
note, and checks the checkout against the list. It never writes reviewer
identity: `review_status` stays `unreviewed` until a person records their
own review by hand (spec 08).

    python3 scripts/publication.py record docs/data/commitments.json \\
        --resource sept11://commitments --freshness monthly --notes "..."
    python3 scripts/publication.py check
    python3 scripts/publication.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sept11.core.errors import Sept11Error  # noqa: E402
from sept11.storage.publication import Publication, record_for  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def cmd_record(root: Path, args) -> int:
    publication = Publication.load(root)
    artifact = publication.record_generated(args.path, args.resource, args.freshness, args.notes)
    print(f"recorded {artifact.path} as {artifact.resource or '(no resource)'} "
          f"sha256 {artifact.sha256[:12]} review_status {artifact.review_status}")
    return 0


def cmd_check(root: Path, args) -> int:
    publication = Publication.load(root)
    found = publication.problems()
    for problem in found:
        print("PROBLEM:", problem)
    print(f"examined {len(publication.artifacts)} allowlisted files and docs/data; {len(found)} problems")
    if not publication.artifacts:
        return 2
    return 1 if found else 0


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
        (root / "docs" / "data").mkdir(parents=True)
        (root / "review").mkdir()
        (root / "docs" / "data" / "a.json").write_text('{"a": 1}')
        (root / "review" / "publication.json").write_text(json.dumps({"artifacts": [
            record_for(root, "docs/data/a.json", "sept11://a", "daily", "fixture")]}))
        check(cmd_check(root, None) == 0, "a listed, intact file passes")
        (root / "docs" / "data" / "b.json").write_text('{"b": 1}')
        check(cmd_check(root, None) == 1, "an unlisted published file fails")

        class Args:
            path, resource, freshness, notes = "docs/data/b.json", "sept11://b", "monthly", "n"
        check(cmd_record(root, Args) == 0, "record succeeds")
        check(cmd_check(root, None) == 0, "after recording, the gate passes")
        manifest = json.loads((root / "review" / "publication.json").read_text())
        entry = next(a for a in manifest["artifacts"] if a["path"] == "docs/data/b.json")
        check(entry["review_status"] == "unreviewed" and entry["reviewed_by"] is None,
              "recording never invents a reviewer")
        # An approved entry is not silently re-pointed at new bytes.
        entry_a = next(a for a in manifest["artifacts"] if a["path"] == "docs/data/a.json")
        entry_a["review_status"], entry_a["reviewed_by"] = "approved", "a person"
        (root / "review" / "publication.json").write_text(json.dumps(manifest))
        (root / "docs" / "data" / "a.json").write_text('{"a": 2}')

        class ArgsA:
            path, resource, freshness, notes = "docs/data/a.json", "sept11://a", "daily", "n"
        try:
            cmd_record(root, ArgsA)
            check(False, "re-recording an approved, changed file must be refused")
        except Sept11Error:
            check(True, "approved entries are protected")
    for message in failures:
        print("FAIL:", message)
    print(f"publication selftest: {len(failures)} failures, {checks - len(failures)}/{checks} checks passed "
          "over a 2-file fixture allowlist")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    rec = sub.add_parser("record", help="record a generated file's current hash on the allowlist")
    rec.add_argument("path", help="path relative to the checkout, e.g. docs/data/commitments.json")
    rec.add_argument("--resource", default=None, help="MCP resource URI, e.g. sept11://commitments")
    rec.add_argument("--freshness", default="on-change", help="daily | monthly | on-change | unknown")
    rec.add_argument("--notes", default="")
    sub.add_parser("check", help="fail on unlisted, missing or changed files")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.cmd:
        ap.print_help()
        return 2
    try:
        return {"record": cmd_record, "check": cmd_check}[args.cmd](ROOT, args)
    except Sept11Error as exc:
        print(f"publication: {exc.code}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
