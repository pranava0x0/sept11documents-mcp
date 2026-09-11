# September 11th Documents Toolkit

Independent research tools for New York City's [September 11th Document Portal](https://sept11documents.cityofnewyork.us/). The local MCP server searches catalog metadata, reads captured pages and checks quotations. The watchdog compares accepted catalog captures and reports observed changes. The site is at [pranava0x0.github.io/sept11documents-mcp](https://pranava0x0.github.io/sept11documents-mcp/).

**Purpose.** This toolkit was built so that New Yorkers, first responders, survivors and their families can read what the City's records show about the air in Lower Manhattan after September 11, 2001. It also lets them check what the City has committed to in releasing those records. Each quote is verbatim from a captured source, a Bates-numbered portal page or an official page, and the build refuses one it cannot locate there.

This is an independent project. It is not an official City of New York website, is not affiliated with 9/11 Health Watch or any official, and provides no legal advice.

## The site

`docs/` is the GitHub Pages site. `make site` serves a local preview at [localhost:8000](http://localhost:8000). The pages, in menu order:

- **Overview** (`index.html`): the portal, the purpose, the commitments quoted from the stipulation, Resolution 560-A, the September 8 transcript and release and DOI's letters, the three announced amounts, the obligation rows and the watchdog capture.
- **The MCP** (`toolkit.html`): the tools, recorded exchanges with the local server, the commands and the portal's API.
- **Examples** (`examples.html`): four demos built on the MCP. Find a record and search the archive's 4,173 folder labels in the browser. Read which official records prove presence for the WTC Health Program and the VCF and who in the City issues them, every rule quoted from the captured official page. Follow each announced commitment (portal $34.2 million, DOI $4 million, Memorial education $1 million) through five separately evidenced stages. Check the saved archive capture beside the dated obligations.
- **Records** (`records.html`): the documents located so far, each with its quote and Bates page.

The demos read dated files in `docs/data/` and call no live server. The evidence guide that routes a person, the building index and the reviewed timeline remain planned.

## The MCP server

```bash
python3 scripts/sept11_mcp.py doctor                    # what this machine holds; no network
python3 scripts/watchdog.py run                         # one catalog export, stored as the local snapshot
python3 scripts/sept11_mcp.py call catalog_search --args '{"folder": "John Street", "count": 5}'
python3 scripts/sept11_mcp.py configure claude-code --dry-run   # also claude, cursor, vscode, codex
python3 scripts/portal_api.py search '"Clean Up Initiative" extension:pdf' --count 5
```

The server uses the Python standard library only. `uv pip install .` installs a `sept11-mcp` command; point it at a checkout with `SEPT11_ROOT`. The MCP registry entry is `server.json`.

The server answers from what this machine has captured: the accepted catalog snapshot, captured page text and the files on the publication allowlist. A fresh clone holds no catalog snapshot until `python3 scripts/watchdog.py run` makes one export. A document whose page text has not been captured returns `not_cached`. `portal_search` reaches the City only when enabled with `SEPT11_ALLOW_LIVE=1`; its response identifies the recipient and the query.

## Layout

| Path | Contents |
|---|---|
| `sept11/` | The package: `core/` evidence and citation models, `adapters/portal.py`, `storage/` (immutable snapshots, cache, budgets, publication allowlist), `mcp/` (stdio server) |
| `scripts/` | Thin CLI entrypoints, listed in `scripts/README.md` |
| `schemas/` | `envelope.schema.json`, generated from the models; every tool result validates against it |
| `docs/` | The GitHub Pages site: four pages, one stylesheet, one script, and the published data in `docs/data/` |
| `review/` | `anchors.json` (located documents) and `publication.json` (the allowlist of files any public surface may serve) |
| `evals/` | `golden.json`, the golden evals seeded from `review/anchors.json` |
| `research/claims/claims.json` | The claim registry: each quote and number on the site with its source |
| `research/sources_manifest.json` | The primary sources the server can cite by id, with their URLs |

## Checks

`make check-public` runs every gate this repository can satisfy on its own; CI runs it on each push. `make check` adds claim verification, the golden evals and the freshness gates for generated pages. Those read captured evidence, including the page text of City documents, which stays out of the repository because it can contain personal information about private individuals.

## Sources

Every number on the site traces to a record in `research/claims/claims.json`, and each primary source is listed with its URL in `research/sources_manifest.json`. Records on the portal are public records of the City of New York; this project links to the City's copies and does not re-host them.
