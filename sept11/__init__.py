"""sept11: read-only core for the September 11th Document Portal toolkit.

Layering (spec/10-security-and-runtime-architecture.md):

    core/      evidence, citation, PII and text models; knows nothing about MCP
    adapters/  bounded clients for outside systems (the City's portal)
    storage/   immutable snapshots, content-addressed cache, budgets, publication allowlist
    mcp/       a small stdio adapter over core; no fetching, no shell, no filesystem tools

`scripts/` holds thin CLI entrypoints only.
"""
from __future__ import annotations

__version__ = "0.2.0"

# Bumped whenever a tool's response envelope changes shape (spec 08, MCP response contract).
SCHEMA_VERSION = "2026-09-11"

# Recorded in every snapshot manifest so snapshots parsed by different code are never compared.
ADAPTER_VERSION = "portal/0.1.0"
