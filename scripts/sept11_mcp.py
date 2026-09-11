#!/usr/bin/env python3
"""sept11_mcp.py: the read-only MCP server for the September 11th Document Portal, and its CLI.

    python3 scripts/sept11_mcp.py                  # serve on stdio (what a client runs)
    python3 scripts/sept11_mcp.py tools            # list the tools; --json for full definitions
    python3 scripts/sept11_mcp.py call catalog_search --args '{"folder": "John Street"}'
    python3 scripts/sept11_mcp.py configure claude-code --dry-run
    python3 scripts/sept11_mcp.py doctor

Transport is stdio; stdout carries protocol frames only and every log line goes
to stderr. Environment: SEPT11_ROOT, SEPT11_PORTAL_BASE, SEPT11_CACHE_DIR,
SEPT11_SNAPSHOT_DIR, SEPT11_PAGE_TEXT_DIR, SEPT11_DATA_DIR, SEPT11_ALLOW_LIVE,
SEPT11_BYTE_BUDGET_MB. The logic lives in `sept11/cli.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sept11.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
