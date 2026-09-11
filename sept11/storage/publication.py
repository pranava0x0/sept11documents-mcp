"""The reviewed publication allowlist (spec 10, "Decisions and boundaries").

Public surfaces — the Pages build and the MCP server's resources — serve files
named in `review/publication.json` and nothing else. Nothing recursively copies
`research/`, `data/` or `review/`, so raw documents, OCR output, review notes and
query history cannot reach a public bundle by being in the wrong directory.

An entry's `review_status` travels with the data. Automated output is
`unreviewed`; only a person writing their identity into the record makes it
`approved`, and automated matching can never fill that in (spec 08).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import IntegrityError, PolicyError
from ..core.evidence import REVIEW
from .cache import sha256_bytes, write_atomic

MANIFEST_NAME = "publication.json"


def _validate_relative_path(value: str) -> str:
    rel = Path(value)
    if not value or rel.is_absolute() or ".." in rel.parts:
        raise IntegrityError(f"publication path must stay relative to the checkout: {value!r}")
    return value


@dataclass(frozen=True)
class Artifact:
    path: str
    resource: str | None
    review_status: str
    reviewed_by: str | None
    reviewed_at: str | None
    sha256: str
    freshness_policy: str
    notes: str = ""

    def as_record(self) -> dict:
        return {"path": self.path, "resource": self.resource, "review_status": self.review_status,
                "reviewed_by": self.reviewed_by, "reviewed_at": self.reviewed_at,
                "sha256": self.sha256, "freshness_policy": self.freshness_policy, "notes": self.notes}


class Publication:
    def __init__(self, root: Path, manifest: dict):
        self.root = Path(root)
        self.manifest = manifest
        self.artifacts: dict[str, Artifact] = {}
        for record in manifest.get("artifacts", []):
            _validate_relative_path(record.get("path", ""))
            if record.get("review_status") not in REVIEW:
                raise IntegrityError(
                    f"{record.get('path')}: review_status {record.get('review_status')!r} is not one of {REVIEW}")
            artifact = Artifact(
                path=record["path"], resource=record.get("resource"),
                review_status=record["review_status"], reviewed_by=record.get("reviewed_by"),
                reviewed_at=record.get("reviewed_at"), sha256=record["sha256"],
                freshness_policy=record.get("freshness_policy", "unknown"), notes=record.get("notes", ""))
            if artifact.path in self.artifacts:
                raise IntegrityError(f"publication path appears more than once: {artifact.path}")
            self.artifacts[artifact.path] = artifact

    @classmethod
    def load(cls, root: Path) -> "Publication":
        path = Path(root) / "review" / MANIFEST_NAME
        if not path.is_file():
            raise IntegrityError(f"no publication allowlist at {path}; nothing may be served")
        return cls(root, json.loads(path.read_text()))

    def by_resource(self, uri: str) -> Artifact:
        for artifact in self.artifacts.values():
            if artifact.resource == uri:
                return artifact
        raise PolicyError(f"{uri} is not on the publication allowlist")

    def read(self, artifact: Artifact) -> bytes:
        """Read an allowlisted file, refusing bytes that differ from the reviewed hash."""
        path = self.root / artifact.path
        if path.is_symlink():
            raise IntegrityError(f"{artifact.path} is a symlink; refusing a path outside the checkout")
        if not path.is_file():
            raise IntegrityError(f"{artifact.path} is on the allowlist but missing from the checkout")
        data = path.read_bytes()
        digest = sha256_bytes(data)
        if digest != artifact.sha256:
            raise IntegrityError(
                f"{artifact.path} changed since it was reviewed "
                f"({artifact.sha256[:12]} -> {digest[:12]}); re-record it before serving")
        return data

    def read_json(self, uri: str) -> tuple[dict, Artifact]:
        artifact = self.by_resource(uri)
        return json.loads(self.read(artifact).decode("utf-8")), artifact

    # -- gate ---------------------------------------------------------------
    def problems(self, published_dirs: tuple[str, ...] = ("docs/data",)) -> list[str]:
        """Everything wrong with the current checkout, in one pass. Empty means the gate passes."""
        found: list[str] = []
        for artifact in self.artifacts.values():
            path = self.root / artifact.path
            if path.is_symlink():
                found.append(f"symlink: {artifact.path} is allowlisted but is not a regular file")
            elif not path.is_file():
                found.append(f"missing: {artifact.path} is allowlisted but not in the checkout")
                continue
            digest = sha256_bytes(path.read_bytes())
            if digest != artifact.sha256:
                found.append(f"changed: {artifact.path} hashes {digest[:12]}, allowlist says {artifact.sha256[:12]}")
        allowlisted = set(self.artifacts)
        for directory in published_dirs:
            base = self.root / directory
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if path.is_file() and str(path.relative_to(self.root)) not in allowlisted:
                    found.append(f"unlisted: {path.relative_to(self.root)} is published but not on the allowlist")
        return found


    def record_generated(self, relative_path: str, resource: str | None, freshness_policy: str,
                         notes: str = "") -> Artifact:
        """Re-record a file a named job regenerates (the watchdog's published output).

        An entry a person has approved is never silently re-pointed at new bytes: that
        would let an automated run inherit a human's review. Such an entry is refused
        until someone re-reviews it and records that themselves.
        """
        _validate_relative_path(relative_path)
        existing = self.artifacts.get(relative_path)
        record = record_for(self.root, relative_path, resource, freshness_policy, notes)
        if existing is not None and existing.review_status == "approved" and existing.sha256 != record["sha256"]:
            raise PolicyError(
                f"{relative_path} is approved at {existing.sha256[:12]} but has changed; "
                "a person must re-review it before it is served again")
        artifact = Artifact(**record)
        self.artifacts[relative_path] = artifact
        records = [a.as_record() for a in self.artifacts.values()]
        self.manifest["artifacts"] = sorted(records, key=lambda r: r["path"])
        path = self.root / "review" / MANIFEST_NAME
        write_atomic(path, (json.dumps(self.manifest, indent=2) + "\n").encode("utf-8"))
        return artifact


def record_for(root: Path, relative_path: str, resource: str | None, freshness_policy: str,
               notes: str = "") -> dict:
    """Build an allowlist record for a file as it stands. Review identity stays empty."""
    data = (Path(root) / relative_path).read_bytes()
    return Artifact(path=relative_path, resource=resource, review_status="unreviewed",
                    reviewed_by=None, reviewed_at=None, sha256=sha256_bytes(data),
                    freshness_policy=freshness_policy, notes=notes).as_record()
