"""Runtime configuration. Every limit here is an engineering budget, not a portal guarantee."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# The checkout that holds the evidence: docs/data, review/ and research/. An installed
# package points at one with SEPT11_ROOT; a checkout finds itself.
ROOT = Path(os.environ.get("SEPT11_ROOT") or Path(__file__).resolve().parent.parent).expanduser()

# Response-contract bounds (spec 08, "MCP response contract").
MAX_QUERY_CHARS = 2_000
MAX_CLAIMS_PER_CALL = 20
MAX_RESULTS = 50
MAX_BROWSE_CHILDREN = 60
MAX_PAGE_TEXT_CHARS = 40_000
TOOL_DEADLINE_SECONDS = 60.0
CURSOR_TTL_SECONDS = 900.0

# Watchdog acceptance (spec 08, "Watchdog state machine" step 2).
DECREASE_QUARANTINE_FRACTION = 0.05


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    """Where local state lives and what the process is allowed to do."""

    root: Path = ROOT
    cache_dir: Path = field(default_factory=lambda: _env_path(
        "SEPT11_CACHE_DIR", Path.home() / ".cache" / "sept11-mcp"))
    # The snapshot store's root; captures live in <root>/snapshots/, derived indexes in <root>/index/.
    snapshot_dir: Path = field(default_factory=lambda: _env_path(
        "SEPT11_SNAPSHOT_DIR", ROOT / "data" / "watchdog"))
    # Reviewed, publishable artifacts. Never research/, data/ or review/ (spec 10).
    data_dir: Path = field(default_factory=lambda: _env_path("SEPT11_DATA_DIR", ROOT / "docs" / "data"))
    # Captured page text from the portal, one file per document with ===== PAGE n ===== markers.
    page_text_dir: Path = field(default_factory=lambda: _env_path(
        "SEPT11_PAGE_TEXT_DIR", ROOT / "research" / "raw" / "sources" / "portal"))
    portal_base: str = field(default_factory=lambda: os.environ.get(
        "SEPT11_PORTAL_BASE", "https://sept11documents.cityofnewyork.us"))
    # Where the golden evals live (spec 02 §5); a file, not a directory a caller can name.
    evals_file: Path = field(default_factory=lambda: ROOT / "evals" / "golden.json")
    # Live search sends the user's terms to the City. It is an explicit opt-in;
    # the safe default is local metadata search only.
    allow_live_search: bool = field(default_factory=lambda: _env_flag("SEPT11_ALLOW_LIVE", False))
    # Unreviewed extractions stay out of tool output unless a maintainer opts in.
    allow_unreviewed: bool = field(default_factory=lambda: _env_flag("SEPT11_ALLOW_UNREVIEWED", False))
    byte_budget_mb: int = field(default_factory=lambda: int(os.environ.get("SEPT11_BYTE_BUDGET_MB", "500")))

    @property
    def byte_budget(self) -> int:
        return self.byte_budget_mb * 1024 * 1024


def load() -> Config:
    return Config()
