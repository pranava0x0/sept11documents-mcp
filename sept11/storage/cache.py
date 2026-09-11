"""Local, content-addressed storage for bytes we have observed.

Two stores, deliberately separate:

    BlobCache      bytes this toolkit fetched, addressed by sha256
    EvidenceStore  captured page text a person can cite, read-only at serving time

Neither reports "now" as the time of capture: a citation that says the page was
retrieved at call time when the bytes are months old is a false provenance claim
(spec 10, "Cache → citation").
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import IntegrityError, NotCachedError
from ..core.text import split_pages


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


def write_atomic(path: Path, data: bytes) -> Path:
    """Write via a temp file in the same directory, then rename. Never a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise IntegrityError(f"refusing to write through a symlink: {path.name}")
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-", delete=False) as tmp:
            tmp_name = tmp.name
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return path


class BlobCache:
    """sha256-addressed bytes plus a provenance record per blob."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _blob_path(self, digest: str) -> Path:
        return self.root / "blobs" / digest[:2] / digest

    def put(self, data: bytes, source_url: str, content_type: str = "") -> dict:
        digest = sha256_bytes(data)
        path = self._blob_path(digest)
        if not path.exists():
            write_atomic(path, data)
        record = {"sha256": digest, "bytes": len(data), "source_url": source_url,
                  "content_type": content_type, "observed_at": _utc(dt.datetime.now().timestamp())}
        write_atomic(path.with_suffix(".json"), json.dumps(record, indent=2).encode("utf-8"))
        return record

    def get(self, digest: str) -> tuple[bytes, dict]:
        path = self._blob_path(digest)
        if not path.exists():
            raise NotCachedError(f"blob {digest[:12]} is not cached")
        data = path.read_bytes()
        if sha256_bytes(data) != digest:
            raise IntegrityError(f"cached blob {digest[:12]} does not match its own hash")
        record_path = path.with_suffix(".json")
        record = json.loads(record_path.read_text()) if record_path.exists() else {}
        return data, record


@dataclass(frozen=True)
class CapturedPage:
    bates: str
    page: int
    text: str
    pages_available: int
    captured_at: str
    text_sha256: str
    provenance: str


class EvidenceStore:
    """Captured portal page text: `<BATES>.txt` with `===== PAGE n =====` markers.

    Serving surfaces read this store and never fetch (spec 10, "Reliability"):
    a missing document is `not_cached` with acquisition instructions, not a
    synchronous download of an arbitrary PDF inside a tool call.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def path_for(self, bates: str) -> Path:
        # `bates` is validated by core.citations.normalize_bates before it reaches here,
        # so it cannot contain a path separator or traversal segment.
        return self.root / f"{bates}.txt"

    def has(self, bates: str) -> bool:
        path = self.path_for(bates)
        if path.is_symlink():
            raise IntegrityError(f"{path.name} is a symlink; refusing a capture outside the cache")
        return path.is_file()

    def documents(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("NYC-WTC_*.txt")) if self.root.is_dir() else []

    def page(self, bates: str, page: int) -> CapturedPage:
        path = self.path_for(bates)
        if path.is_symlink():
            raise IntegrityError(f"{path.name} is a symlink; refusing a capture outside the cache")
        if not path.is_file():
            raise NotCachedError(
                f"{bates}: no captured text locally. Fetch and extract it first "
                f"(python3 scripts/portal_api.py fetch {bates}), then re-ask.")
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        pages = split_pages(text)
        if not pages:
            raise NotCachedError(f"{bates}: captured text has no page markers; recapture and extract it first")
        if page not in pages:
            raise NotCachedError(
                f"{bates}: page {page} is not in the captured text "
                f"(captured pages: {min(pages)}-{max(pages)} of an unknown total)")
        return CapturedPage(
            bates=bates, page=page, text=pages[page], pages_available=len(pages),
            captured_at=_utc(path.stat().st_mtime), text_sha256=sha256_bytes(raw),
            # No capture ledger exists for portal extracts yet (issues.md); the file's
            # modification time is the honest upper bound on when it was written.
            provenance="local_capture:file_mtime")
