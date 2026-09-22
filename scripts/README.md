# scripts/

Site workflow: `make build-site` updates the four pages (landing, the MCP, the examples, the records) from
the tool registry, the allowlisted data files and the claim registry. `make site` serves the preview. `make demo-check` runs browser acceptance using
Playwright (install separately or set `NODE_PATH` to an existing installation). Set `CHROME_PATH` to a
Chrome executable if Playwright's browser is not installed. These optional browser dependencies do not
affect the stdlib MCP runtime or offline `make check`. `DEMO_URL` accepts a localhost preview only.

Thin CLI entrypoints over the `sept11/` package (spec 10). The client, evidence models, storage and MCP
adapter live in the package; these files parse arguments and format output. Every script has `--selftest`
(offline fixtures) and prints what it examined. Run `make check` from the project root.

| Script | Does | Typical use |
|---|---|---|
| `portal_api.py` | CLI for `sept11.adapters.portal`: `search`, `facets`, `export` (whole catalog in one call), `fetch` (PDF by Bates, validated). HTTPS-only allowlisted hosts, no redirects, 1 req/s and a daily byte budget shared across processes. | `python3 scripts/portal_api.py search '"Clean Up Initiative" extension:pdf' --count 5` |
| `sept11_mcp.py` | The read-only MCP server on stdio (10 tools, 8 resources plus 2 templates, 3 prompts) and its CLI: `serve` (default), `tools [--json]`, `call NAME --args JSON`, `configure {claude-code,claude,cursor,vscode,codex} [--dry-run]`, `doctor`. Logic in `sept11/cli.py`. | `python3 scripts/sept11_mcp.py configure claude-code --dry-run` |
| `mcp_evals.py` | Golden evals from `evals/golden.json` (seeded from `review/anchors.json`): every anchor in the snapshot, its quote located, its stamp read off the page; `--live` (opt-in) adds ≤20 portal searches. Zero anchors examined exits 2. | `make evals` |
| `publication.py` | The allowlist: `record <path> --resource URI` re-hashes a generated file (never inventing a reviewer); `check` fails on unlisted, missing or changed files. | `make publication` |
| `build_folder_index.py` | `docs/data/folders.json` from the accepted snapshot: one row per collection/box/folder with counts and the first Bates; labels PII-screened; `--check` gates staleness. | `make folders` |
| `evidence_bundle.py` | The gitignored inputs `make check` reads (captured official pages, catalog CSVs, the snapshot store), packed with a SHA-256 manifest; `restore` refuses a member whose hash differs or whose path escapes the root; `check` names what a checkout lacks. | `python3 scripts/evidence_bundle.py pack --out evidence-bundle.tgz` |
| `record_mcp_examples.py` | `docs/data/mcp-examples.json`: real calls to the local server, envelopes unchanged except elided page text; refuses to run with live search on; `--check` gates staleness. | `make examples` |
| `watchdog.py` | The release watchdog: one export → immutable snapshot → accept or quarantine → atomic publish of `docs/data/watchdog/latest.json`. `import` ingests an existing capture; `status` lists snapshots. | `make watchdog` |
| `security_checks.py` | Hostile-input regression suite for the portal client: origin contract, redirect refusal, response caps, catalog integrity, paging cursors, PDF cache rejection. | `python3 scripts/security_checks.py --selftest` |
| `build_site.py` | Writes the menu, footer and hashed asset URLs into all four pages, the figures, the tool table from the MCP registry, and the ten demos from allowlisted data (anchors, the help directory, the commitment ledger, the scorecard, catalog summary, a dated context scan, and recorded examples). Every displayed quote must be its claim's registered quote, located in the captured source; a ledger with a total never reaches a page. `--check` gates it. | `make build-site` |
| `site_checks.py` | Structural gates for every page and the shared stylesheet: column labels, colgroups and their width classes, the stacked breakpoint, the motion ceiling, the grid-overflow trap. | `python3 scripts/site_checks.py --selftest` |
| `mcp_checks.py` | Local-core and stdio gates for the server: protocol shape, argument bounds, cursor binding, untrusted document text, the PII stop rule, the publication allowlist. | `python3 scripts/mcp_checks.py --selftest` |
| `export_catalog.py` | Daily snapshot → `research/raw/portal-recon/catalog_<date>_pdf.csv` + `_summary.json`, then a diff against the previous snapshot. | `make catalog` |
| `catalog_diff.py` | Added / removed / changed documents between two snapshots (markdown + JSON). | `python3 scripts/catalog_diff.py old.csv new.csv --md report.md` |
| `verify_claims.py` | Evidence-location checking of every published number/quote against captured evidence (portal page text or cached source text); `--online` probes URLs (live / blocked / dead). | `make verify` |
| `fetch_sources.py` | (Re)captures the primary sources in `research/sources_manifest.json` with sha256 provenance; extracts PDF text with pypdf; classifies failures. | `make sources` |

## Refreshing the evidence

1. `make sources` refreshes the primaries; it adds nothing new unless the manifest changed.
2. `make catalog` takes a new snapshot and diffs it against the previous one.
3. Add every new number you intend to publish to `research/claims/claims.json`, then run `make verify`.

## The MCP server

`scripts/sept11_mcp.py` serves what is on this machine: the accepted catalog snapshots under `data/watchdog/`,
the captured page text under `research/raw/sources/portal/`, and the files on the publication allowlist
(`review/publication.json`). It fetches nothing during a tool call — an uncaptured document returns
`not_cached` with the command that captures it — and `portal_search` is the one tool that reaches the City,
labelled with the recipient and the exact query sent. `python3 scripts/sept11_mcp.py doctor` reports what
the machine holds. Installed with `uv pip install .`, the `sept11-mcp` command needs `SEPT11_ROOT` set to a
checkout. Bootstrap a fresh checkout with:

```
python3 scripts/watchdog.py run                                  # or: import an existing CSV capture
python3 scripts/watchdog.py import catalog.csv --captured-at 2026-09-09T15:30:00Z
```

Strict claim checking rejects every outcome except `found`. A found text match is not semantic approval. `catalog` records recompute document/page totals from a SHA-256-pinned CSV; all other quantitative claims require quoted evidence. Missing captured files fail. See spec 08 for remaining publication gates.

Live search is opt-in. Set `SEPT11_ALLOW_LIVE=1` only when sending the query to the City's portal is acceptable;
the default is disabled so local MCP use does not disclose search terms upstream.

CI: `.github/workflows/check.yml` runs `make check-public` on every push and pull request. The full `make check` reads captures that stay out of the repository and runs in a maintainer's checkout. `.github/workflows/watchdog.yml` runs the daily export, keeps the snapshot store as a release asset, and commits `docs/data/watchdog/latest.json`; it stays disabled until its first run is reviewed. Both pin their actions by commit SHA.
