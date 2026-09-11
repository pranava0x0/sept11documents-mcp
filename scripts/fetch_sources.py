#!/usr/bin/env python3
"""fetch_sources.py: (re)capture the primary sources this project cites.

Reads research/sources_manifest.json (a list of {id, url, filename, kind,
notes}), downloads anything missing into research/raw/sources/ (PDF) or
research/raw/sources/html/ (HTML), bounds each response at 64 MiB, and validates the bytes (a PDF must start with
%PDF-; an HTML capture must not be a bot-wall page), extracts PDF text with
pypdf when it is importable (`uv run --with pypdf python3 scripts/fetch_sources.py`),
and records provenance in research/raw/sources/manifest.lock.json: final URL,
HTTP status, sha256, size, fetched_at, and how it was fetched.

Failures are classified, never collapsed: live / blocked / dead, following the
government-data skill. nyc.gov and cdc.gov answer 403 to a bare client; the
script retries once with a browser-style User-Agent and records that it did.

Usage:
    python3 scripts/fetch_sources.py            # fetch what is missing
    python3 scripts/fetch_sources.py --check    # liveness only, no writes
    python3 scripts/fetch_sources.py --refresh  # re-download everything
    python3 scripts/fetch_sources.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import portal_api  # noqa: E402
from verify_claims import classify_status  # noqa: E402

MANIFEST = ROOT / "research" / "sources_manifest.json"
LOCK = ROOT / "research" / "raw" / "sources" / "manifest.lock.json"
OUT_PDF = ROOT / "research" / "raw" / "sources"
OUT_HTML = ROOT / "research" / "raw" / "sources" / "html"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
WALL_MARKERS = (b"Access Denied", b"Request unsuccessful. Incapsula", b"cf-browser-verification",
                b"Just a moment...", b"Pardon Our Interruption")
MAX_SOURCE_BYTES = 64 * 1024 * 1024


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def looks_like_wall(body: bytes) -> bool:
    head = body[:4000]
    return any(m in head for m in WALL_MARKERS)


def validate(kind: str, body: bytes) -> str | None:
    if kind == "pdf" and body[:5] != b"%PDF-":
        return "not a PDF (magic mismatch)"
    if kind == "html" and looks_like_wall(body):
        return "bot-wall page captured instead of content"
    if len(body) < 200:
        return f"suspiciously small body ({len(body)} bytes)"
    return None


def read_bounded(response, limit: int = MAX_SOURCE_BYTES) -> bytes:
    """Read a source response without allowing an accidental huge body into memory."""
    chunks, total = [], 0
    while True:
        chunk = response.read(min(65536, limit + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise ValueError(f"source response exceeds {limit} byte limit")


def fetch(url: str, timeout: float = 120.0) -> tuple[int | None, bytes, str, str, str | None]:
    """Return (status, body, final_url, user_agent_used, error)."""
    for ua in (portal_api.USER_AGENT, BROWSER_UA):
        req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, read_bounded(r), r.geturl(), ua, None
        except urllib.error.HTTPError as e:
            if e.code == 403 and ua != BROWSER_UA:
                time.sleep(1.5)
                continue
            return e.code, b"", url, ua, None
        except Exception as e:  # noqa: BLE001
            return None, b"", url, ua, str(e)
    return None, b"", url, BROWSER_UA, "unreachable"  # pragma: no cover


def extract_pdf_text(pdf: Path, txt: Path, max_pages: int = 400) -> str | None:
    try:
        import pypdf  # type: ignore
    except ImportError:
        return "pypdf not installed (run under `uv run --with pypdf`)"
    import logging
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        r = pypdf.PdfReader(str(pdf))
        parts = [f"\n\n===== PAGE {i + 1} =====\n{(p.extract_text() or '')}" for i, p in enumerate(r.pages[:max_pages])]
        txt.write_text("".join(parts), encoding="utf-8")
        chars = sum(len(p) for p in parts)
        return None if chars > 200 else "no text layer (scanned; needs OCR)"
    except Exception as e:  # noqa: BLE001
        return f"extract error: {e}"


def run(manifest: list[dict], refresh: bool, check_only: bool, log=print) -> dict:
    lock = json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.exists() else {}
    last_hit: dict[str, float] = {}
    summary = {"examined": 0, "fetched": 0, "skipped": 0, "live": 0, "blocked": 0, "dead": 0, "invalid": 0}
    for entry in manifest:
        summary["examined"] += 1
        sid, url, kind = entry["id"], entry["url"], entry.get("kind", "pdf")
        dest = (OUT_PDF if kind == "pdf" else OUT_HTML) / entry["filename"]
        host = urlparse(url).hostname or ""
        if not check_only and dest.exists() and not refresh:
            summary["skipped"] += 1
            continue
        wait = last_hit.get(host, 0) + 1.5 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        status, body, final, ua, err = fetch(url)
        last_hit[host] = time.monotonic()
        cls = classify_status(status, err)
        summary[cls] += 1
        rec = {"id": sid, "url": url, "final_url": final, "status": status, "error": err, "class": cls,
               "user_agent": "browser-style" if ua == BROWSER_UA else "honest",
               "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        if cls != "live":
            log(f"{sid}: {cls} ({status or err})")
            lock[sid] = {**lock.get(sid, {}), **rec}
            continue
        problem = validate(kind, body)
        if problem:
            summary["invalid"] += 1
            log(f"{sid}: fetched but invalid: {problem}")
            lock[sid] = {**lock.get(sid, {}), **rec, "invalid": problem}
            continue
        if check_only:
            lock[sid] = {**lock.get(sid, {}), **rec}
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Finish the capture before replacing the destination. A killed process
        # must leave the prior capture intact, never a truncated source file.
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=f".{dest.name}.", delete=False) as tmp:
                tmp_name = tmp.name
                tmp.write(body)
            os.replace(tmp_name, dest)
        finally:
            if tmp_name and os.path.exists(tmp_name):
                os.unlink(tmp_name)
        rec.update({"file": str(dest.relative_to(ROOT)), "bytes": len(body), "sha256": sha256(body),
                    "fetched_at": rec["checked_at"], "notes": entry.get("notes", "")})
        if kind == "pdf":
            note = extract_pdf_text(dest, dest.with_suffix(".txt"))
            rec["text_extract"] = note or "ok"
        lock[sid] = rec
        summary["fetched"] += 1
        log(f"{sid}: saved {dest.name} ({len(body)} bytes)")
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps(lock, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def selftest() -> int:
    ok = validate("pdf", b"%PDF-1.4" + b"x" * 300) is None
    ok &= validate("pdf", b"<html>" + b"x" * 300) is not None
    ok &= validate("html", b"<html>Access Denied</html>" + b"x" * 300) is not None
    ok &= validate("html", b"<html><body>real page</body></html>" + b"x" * 300) is None
    ok &= validate("pdf", b"%PDF-") is not None  # too small
    class TooLarge:
        def read(self, n):
            return b"x" * (n or MAX_SOURCE_BYTES + 1)
    try:
        read_bounded(TooLarge(), 32)
        ok = False
    except ValueError:
        pass
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.pdf"; p.write_bytes(b"%PDF-1.4 minimal")
        note = extract_pdf_text(p, p.with_suffix(".txt"))
        ok &= note is not None  # either pypdf missing or unreadable -> a note, never a silent pass
    print(f"fetch_sources selftest: {'ok' if ok else 'FAIL'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true"); ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--only", nargs="*", help="manifest ids to process")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if args.only:
        manifest = [m for m in manifest if m["id"] in set(args.only)]
    if not manifest:
        print("fetch_sources: nothing selected; that is a no-op, not a success", file=sys.stderr); return 2
    s = run(manifest, refresh=args.refresh, check_only=args.check)
    print(json.dumps(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
