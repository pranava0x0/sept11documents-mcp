"""What a tool call is allowed to touch. Built once per process, read-only at serving time."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .. import config
from ..adapters import portal
from ..core.errors import DeadlineError, IntegrityError
from ..storage.budget import Budget
from ..storage.cache import EvidenceStore
from ..storage.publication import Publication
from ..storage.snapshots import SnapshotStore


class Deadline:
    """A wall-clock budget for one tool call, checked between steps."""

    def __init__(self, seconds: float, clock=time.monotonic):
        self.clock = clock
        self.expires_at = clock() + seconds
        self.seconds = seconds

    @property
    def remaining(self) -> float:
        return self.expires_at - self.clock()

    def check(self, what: str) -> None:
        if self.remaining <= 0:
            raise DeadlineError(f"tool budget of {self.seconds:.0f}s exhausted before {what}")


class Context:
    def __init__(self, cfg: config.Config | None = None, client=None, log=None):
        self.cfg = cfg or config.load()
        self.evidence = EvidenceStore(self.cfg.page_text_dir)
        self.snapshots = SnapshotStore(self.cfg.snapshot_dir)
        self.budget = Budget(self.cfg.cache_dir / "budget.json", self.cfg.byte_budget)
        # Logs go to stderr: stdout carries protocol frames only (MCP stdio transport).
        self.log = log or (lambda message: print(message, file=sys.stderr, flush=True))
        self._client = client
        self._catalog: tuple[list[dict], object] | None = None
        self._publication: Publication | None = None
        self._sources: dict[str, dict] | None = None

    def client(self):
        if self._client is None:
            self._client = portal.PortalClient(base=self.cfg.portal_base, budget=self.budget,
                                               log=self.log)
        return self._client

    def publication(self) -> Publication:
        if self._publication is None:
            self._publication = Publication.load(self.cfg.root)
        return self._publication

    def catalog(self) -> tuple[list[dict], object]:
        """Rows of the latest accepted snapshot, with the snapshot itself."""
        if self._catalog is None:
            snapshot = self.snapshots.latest_accepted()
            if snapshot is None:
                raise IntegrityError(
                    "no accepted catalog snapshot on this machine. Run "
                    "`python3 scripts/watchdog.py run` (one export), or import an existing capture with "
                    "`python3 scripts/watchdog.py import <catalog.csv> --captured-at <ISO-8601>`.")
            self._catalog = (snapshot.rows(portal.parse_catalog_csv), snapshot)
        return self._catalog

    def registered_sources(self) -> dict[str, dict]:
        """Primary sources from research/sources_manifest.json, addressed by id only.

        Callers name a source id; they never hand this server a path or a URL to read.
        """
        if self._sources is None:
            path = self.cfg.root / "research" / "sources_manifest.json"
            entries = json.loads(path.read_text()) if path.is_file() else []
            self._sources = {e["id"]: e for e in entries if e.get("id")}
        return self._sources

    def source_text_path(self, source_id: str) -> Path:
        entry = self.registered_sources()[source_id]
        filename = Path(entry["filename"])
        if filename.is_absolute() or ".." in filename.parts:
            raise IntegrityError(f"registered source filename escapes the research tree: {entry['filename']!r}")
        path = self.cfg.root / "research" / "raw" / "sources" / (filename.stem + ".txt")
        if path.is_symlink():
            raise IntegrityError(f"registered source text is a symlink: {path.name}")
        return path
