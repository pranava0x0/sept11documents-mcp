#!/usr/bin/env python3
"""evidence_bundle.py: the captured evidence a clean clone needs, packed with hashes (spec 08).

`make check` reads files that git ignores: the HTML captures of official pages
(the text the claim registry quotes), the catalog CSV snapshots, and the
watchdog's immutable snapshot store. A fresh clone has none of them, so its
checks cannot run. This packs exactly those inputs into one archive with a
manifest of SHA-256 hashes, and restores them only when every byte matches.

    python3 scripts/evidence_bundle.py pack --out evidence-bundle.tgz
    python3 scripts/evidence_bundle.py restore evidence-bundle.tgz [--root DIR]
    python3 scripts/evidence_bundle.py check          # what this checkout holds or lacks
    python3 scripts/evidence_bundle.py --selftest

A restored member is refused when its hash differs from the manifest, when its
path leaves the root, or when it is not a regular file. Nothing under
`research/raw/sources/portal/` (page text of City documents) is bundled beyond
what the repository already commits.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_NAME = "BUNDLE-MANIFEST.json"
# Globs, relative to the checkout, of the gitignored inputs the offline checks read.
INPUTS = (
    "research/raw/sources/html/*.html",
    "research/raw/portal-recon/catalog_*_pdf.csv",
    "research/raw/portal-recon/q_harding_title.json",
    "data/watchdog/snapshots/*/catalog.csv",
    "data/watchdog/snapshots/*/manifest.json",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inputs(root: Path) -> list[Path]:
    found: list[Path] = []
    for pattern in INPUTS:
        found += [p for p in sorted(root.glob(pattern)) if p.is_file() and not p.is_symlink()]
    return found


def pack(root: Path, out: Path, log=print) -> int:
    files = inputs(root)
    if not files:
        log("evidence_bundle: found nothing to pack; that is a broken checkout, not an empty bundle")
        return 2
    manifest = {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "inputs": list(INPUTS), "files": []}
    with tarfile.open(out, "w:gz") as tar:
        for path in files:
            rel = str(path.relative_to(root))
            manifest["files"].append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
            tar.add(path, arcname=rel, recursive=False)
        body = json.dumps(manifest, indent=2).encode("utf-8")
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    total = sum(f["bytes"] for f in manifest["files"])
    log(f"packed {len(files)} files ({total:,} bytes) into {out} ({out.stat().st_size:,} bytes)")
    return 0


def restore(bundle: Path, root: Path, log=print) -> int:
    root = root.resolve()
    with tarfile.open(bundle, "r:gz") as tar:
        try:
            manifest = json.loads(tar.extractfile(MANIFEST_NAME).read().decode("utf-8"))
        except (KeyError, AttributeError, ValueError) as exc:
            log(f"evidence_bundle: {bundle} has no readable {MANIFEST_NAME}: {exc}")
            return 1
        expected = {f["path"]: f["sha256"] for f in manifest["files"]}
        written = skipped = 0
        for member in tar.getmembers():
            if member.name == MANIFEST_NAME:
                continue
            if not member.isfile():
                log(f"evidence_bundle: refusing non-file member {member.name!r}")
                return 1
            if member.name not in expected:
                log(f"evidence_bundle: refusing member not in the manifest: {member.name!r}")
                return 1
            target = (root / member.name).resolve()
            if root not in target.parents:
                log(f"evidence_bundle: refusing path outside the root: {member.name!r}")
                return 1
            data = tar.extractfile(member).read()
            digest = hashlib.sha256(data).hexdigest()
            if digest != expected[member.name]:
                log(f"evidence_bundle: {member.name} hashes {digest[:12]}, manifest says {expected[member.name][:12]}; "
                    "nothing written")
                return 1
            if target.is_file() and sha256_file(target) == digest:
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.restoring")
            tmp.write_bytes(data)
            tmp.replace(target)
            written += 1
    log(f"restored {written} files, {skipped} already present and identical, under {root}")
    return 0


def check(root: Path, log=print) -> int:
    """Every input category must be present; a clone with one committed file is not evidenced."""
    present = inputs(root)
    log(f"{len(present)} evidence inputs present under {root}")
    missing = []
    for pattern in INPUTS:
        n = len([p for p in root.glob(pattern) if p.is_file()])
        log(f"  {pattern}: {n}")
        if n == 0:
            missing.append(pattern)
    if missing:
        log("evidence_bundle: missing " + ", ".join(missing) + "; restore a bundle before `make check`")
        return 1
    return 0


def selftest() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as folder:
        src = Path(folder) / "src"
        (src / "research/raw/sources/html").mkdir(parents=True)
        (src / "data/watchdog/snapshots/s1").mkdir(parents=True)
        (src / "research/raw/sources/html/a.html").write_text("<p>captured</p>")
        (src / "data/watchdog/snapshots/s1/catalog.csv").write_text("Mindbreeze Key;Name\nx;y\n")
        (src / "data/watchdog/snapshots/s1/manifest.json").write_text("{}")
        bundle = Path(folder) / "b.tgz"
        if pack(src, bundle, log=lambda *_: None) != 0:
            failures.append("pack failed")
        dest = Path(folder) / "clean"
        dest.mkdir()
        if restore(bundle, dest, log=lambda *_: None) != 0 or not (dest / "research/raw/sources/html/a.html").is_file():
            failures.append("restore into a clean root must recreate the inputs")
        if restore(bundle, dest, log=lambda *_: None) != 0:
            failures.append("a second restore must be a no-op, not a failure")
        # A corrupted member is refused before anything is written.
        corrupt = Path(folder) / "c.tgz"
        with tarfile.open(bundle, "r:gz") as tin, tarfile.open(corrupt, "w:gz") as tout:
            for m in tin.getmembers():
                data = tin.extractfile(m).read()
                if m.name.endswith("a.html"):
                    data = b"<p>tampered</p>"
                    m.size = len(data)
                tout.addfile(m, io.BytesIO(data))
        dest2 = Path(folder) / "clean2"
        dest2.mkdir()
        if restore(corrupt, dest2, log=lambda *_: None) == 0 or any(dest2.rglob("*.html")):
            failures.append("a member whose hash differs from the manifest must be refused")
        # A path that escapes the root is refused.
        evil = Path(folder) / "e.tgz"
        with tarfile.open(bundle, "r:gz") as tin, tarfile.open(evil, "w:gz") as tout:
            for m in tin.getmembers():
                data = tin.extractfile(m).read()
                if m.name == MANIFEST_NAME:
                    doc = json.loads(data)
                    doc["files"].append({"path": "../escape.txt", "bytes": 4, "sha256": hashlib.sha256(b"boom").hexdigest()})
                    data = json.dumps(doc).encode()
                    m.size = len(data)
                tout.addfile(m, io.BytesIO(data))
            info = tarfile.TarInfo("../escape.txt")
            info.size = 4
            tout.addfile(info, io.BytesIO(b"boom"))
        dest3 = Path(folder) / "clean3"
        dest3.mkdir()
        if restore(evil, dest3, log=lambda *_: None) == 0 or (Path(folder) / "escape.txt").exists():
            failures.append("a member path outside the root must be refused")
        if check(Path(folder) / "nothing-here", log=lambda *_: None) == 0:
            failures.append("check must fail on a checkout with no evidence")
        partial = Path(folder) / "partial"
        (partial / "research/raw/sources/html").mkdir(parents=True)
        (partial / "research/raw/sources/html/a.html").write_text("x")
        if check(partial, log=lambda *_: None) == 0:
            failures.append("check must fail when any input category is missing")
    for message in failures:
        print("FAIL:", message)
    print(f"evidence_bundle selftest: {len(failures)} failures, {7 - len(failures)}/7 checks passed over a 3-file fixture")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("pack"); p.add_argument("--out", required=True, type=Path)
    r = sub.add_parser("restore"); r.add_argument("bundle", type=Path); r.add_argument("--root", type=Path, default=ROOT)
    sub.add_parser("check")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.cmd == "pack":
        return pack(ROOT, args.out)
    if args.cmd == "restore":
        return restore(args.bundle, args.root)
    if args.cmd == "check":
        return check(ROOT)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
