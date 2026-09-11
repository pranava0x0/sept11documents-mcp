"""Immutable catalog snapshots and the watchdog state machine (spec 08).

Rules this module exists to enforce:

  * a snapshot directory is written once and never rewritten, so a second run
    on the same day cannot overwrite the morning's evidence;
  * a candidate is accepted or quarantined, and only accepted snapshots are
    compared or published;
  * the published pointer moves atomically, after the payload is written, so a
    failed run leaves the last good output in place;
  * `baseline_at_or_before` returns the interval it actually used and says when
    there is a gap, instead of silently answering with the wrong window.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .. import ADAPTER_VERSION
from ..config import DECREASE_QUARANTINE_FRACTION
from ..core.catalog import diff_catalogs
from ..core.errors import IntegrityError, InputError
from .cache import sha256_bytes, write_atomic

ACCEPTED, QUARANTINED = "accepted", "quarantined"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _parse_ts(value: str) -> dt.datetime:
    text = str(value).strip()
    try:
        if len(text) == 10:
            # A bare date means the end of that day in UTC, exclusive: a capture stamped
            # midnight of the next day is not "at or before" it.
            return (dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc)
                    + dt.timedelta(days=1) - dt.timedelta(microseconds=1))
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InputError(f"not an ISO-8601 date or timestamp: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    path: Path
    manifest: dict

    @property
    def captured_at(self) -> str:
        return self.manifest["captured_at"]

    @property
    def status(self) -> str:
        return self.manifest["status"]

    def rows(self, parse) -> list[dict]:
        csv_path = self.path / "catalog.csv"
        raw = csv_path.read_bytes()
        if sha256_bytes(raw) != self.manifest["csv_sha256"]:
            raise IntegrityError(f"{self.snapshot_id}: stored CSV does not match its manifest hash")
        return parse(raw.decode("utf-8"))


class RunLock:
    """One watchdog run at a time. O_EXCL create is atomic; check-then-touch is not."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: int | None = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise IntegrityError(
                f"another run holds {self.path}; remove it only after confirming no run is active") from exc
        os.write(self._fd, f'{{"pid": {os.getpid()}, "started": "{_now()}"}}'.encode())
        return self

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self.path.unlink(missing_ok=True)


class SnapshotStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def snapshots_dir(self) -> Path:
        return self.root / "snapshots"

    def lock(self) -> RunLock:
        return RunLock(self.root / "watchdog.lock")

    # -- reading ------------------------------------------------------------
    def all(self) -> list[Snapshot]:
        if not self.snapshots_dir.is_dir():
            return []
        out = []
        for path in sorted(self.snapshots_dir.iterdir()):
            manifest_path = path / "manifest.json"
            if manifest_path.is_file():
                out.append(Snapshot(path.name, path, json.loads(manifest_path.read_text())))
        return sorted(out, key=lambda s: s.captured_at)

    def accepted(self) -> list[Snapshot]:
        return [s for s in self.all() if s.status == ACCEPTED]

    def latest_accepted(self) -> Snapshot | None:
        accepted = self.accepted()
        return accepted[-1] if accepted else None

    def baseline_at_or_before(self, when: str) -> tuple[Snapshot | None, dict]:
        """Newest accepted snapshot at or before `when`, plus what that window really covers."""
        cutoff = _parse_ts(when)
        accepted = self.accepted()
        earlier = [s for s in accepted if _parse_ts(s.captured_at) <= cutoff]
        if not earlier:
            oldest = accepted[0].captured_at if accepted else None
            return None, {"requested": when, "baseline": None, "coverage": "unknown",
                          "gap": f"no accepted snapshot at or before {when}"
                                 + (f"; the oldest accepted snapshot is {oldest}" if oldest else
                                    "; no accepted snapshots exist")}
        baseline = earlier[-1]
        gap_days = (cutoff - _parse_ts(baseline.captured_at)).total_seconds() / 86400
        note = {"requested": when, "baseline": baseline.captured_at, "coverage": "partial"}
        if gap_days > 1.5:
            note["gap"] = (f"the nearest accepted snapshot is {gap_days:.1f} days before {when}; "
                           "changes inside that window are not observed here")
        return baseline, note

    # -- writing ------------------------------------------------------------
    def add(self, csv_text: str, query: str, parse, captured_at: str | None = None,
            adapter_version: str = ADAPTER_VERSION, http_status: int | None = None,
            raw_bytes: bytes | None = None) -> Snapshot:
        """Store a candidate immutably, then accept or quarantine it. Never overwrites.

        `raw_bytes` preserves the captured bytes exactly. Re-encoding decoded text
        changes the digest — a BOM alone is enough — and two digests for one artifact
        read as two different captures.
        """
        captured_at = captured_at or _now()
        raw = raw_bytes if raw_bytes is not None else csv_text.encode("utf-8")
        digest = sha256_bytes(raw)
        stamp = _parse_ts(captured_at).strftime("%Y%m%dT%H%M%SZ")
        snapshot_id = f"{stamp}-{digest[:12]}"
        path = self.snapshots_dir / snapshot_id
        if path.exists():
            raise IntegrityError(f"snapshot {snapshot_id} already exists; snapshots are immutable")

        warnings: list[str] = []
        status, reason = ACCEPTED, None
        rows: list[dict] = []
        try:
            rows = parse(csv_text)
        except Exception as exc:  # a malformed export is evidence too: store it, quarantined
            status, reason = QUARANTINED, f"parse_failed: {exc}"
        if status == ACCEPTED and not rows:
            status, reason = QUARANTINED, "empty_export"

        previous = self.latest_accepted()
        decrease = None
        if status == ACCEPTED and previous is not None:
            if previous.manifest.get("query") != query:
                status, reason = QUARANTINED, "query_scope_differs_from_last_accepted"
            else:
                before = previous.manifest["documents"]
                if before and len(rows) < before:
                    decrease = (before - len(rows)) / before
                    if decrease > DECREASE_QUARANTINE_FRACTION:
                        status, reason = QUARANTINED, (
                            f"document count fell {decrease:.1%} ({before} -> {len(rows)}); "
                            "held for review; it is not reported as removal")
                    else:
                        warnings.append(f"document count fell {decrease:.1%} ({before} -> {len(rows)})")

        manifest = {
            "snapshot_id": snapshot_id, "captured_at": captured_at, "query": query,
            "adapter_version": adapter_version, "http_status": http_status,
            "csv_sha256": digest, "csv_bytes": len(raw),
            "bytes_origin": "captured file" if raw_bytes is not None else "decoded response, re-encoded utf-8",
            "documents": len(rows), "pages": sum(r.get("page_count") or 0 for r in rows),
            "pdf_bytes": sum(r.get("pdf_size") or 0 for r in rows),
            "status": status, "reason": reason, "warnings": warnings,
            "previous_accepted": previous.snapshot_id if previous else None,
            "decrease_fraction": round(decrease, 4) if decrease is not None else None,
        }
        path.mkdir(parents=True)
        write_atomic(path / "catalog.csv", raw)
        write_atomic(path / "manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
        return Snapshot(snapshot_id, path, manifest)

    def bates_index(self, snapshot: Snapshot, parse) -> set[str]:
        """Document IDs in a snapshot, cached beside the store. Derived data, never evidence."""
        cached = self.root / "index" / f"{snapshot.snapshot_id}.txt"
        if cached.is_file():
            return set(cached.read_text().split())
        ids = sorted(row["bates"] for row in snapshot.rows(parse))
        write_atomic(cached, ("\n".join(ids) + "\n").encode("utf-8"))
        return set(ids)

    # -- comparison ---------------------------------------------------------
    def changes_since(self, when: str, parse) -> dict:
        """Observed differences between the baseline at or before `when` and the latest accepted."""
        latest = self.latest_accepted()
        if latest is None:
            raise IntegrityError("no accepted snapshot exists yet; run the watchdog first")
        baseline, note = self.baseline_at_or_before(when)
        if baseline is None:
            raise IntegrityError(note["gap"])
        if baseline.snapshot_id == latest.snapshot_id:
            raise IntegrityError(
                f"the only accepted snapshot at or before {when} is also the latest ({latest.captured_at}); "
                "there is no interval to compare")
        if baseline.manifest.get("query") != latest.manifest.get("query"):
            raise IntegrityError("snapshots were taken with different query scopes; not comparable")
        report = diff_catalogs(baseline.rows(parse), latest.rows(parse))
        return {
            "interval": {"from": baseline.captured_at, "to": latest.captured_at,
                         "from_snapshot": baseline.snapshot_id, "to_snapshot": latest.snapshot_id,
                         **{k: v for k, v in note.items() if k == "gap"}},
            "query": latest.manifest.get("query"),
            "observed_added": report["added"],
            "observed_absent": report["removed"],
            "metadata_changed": report["changed"],
            "pages_added": report["pages_added"], "pages_absent": report["pages_removed"],
            "added_by_source": report["added_by_source"], "added_by_box": report["added_by_box"],
            "documents": {"from": report["old_documents"], "to": report["new_documents"]},
            "note": ("Absence in a later catalog is an observation only; deletion, redaction and withholding "
                     "are separate findings it cannot make. Catalog rows do not show PDF byte changes."),
        }

    # -- publication --------------------------------------------------------
    def publish(self, target: Path, payload: dict) -> Path:
        """Write the payload, then move the pointer. A failed run keeps the last good file."""
        target = Path(target)
        staged = target.with_name(target.name + ".staged")
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        write_atomic(staged, body)
        json.loads(staged.read_text())  # never point at something that will not parse
        os.replace(staged, target)
        return target
