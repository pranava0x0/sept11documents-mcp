.PHONY: check check-public selftest index catalog verify claims-online site site-check build-site mcp mcp-tools watchdog
.PHONY: demo-check evals publication examples folders doctor prose

demo-check:                      ## browser acceptance; run make site first, provide Playwright
	node scripts/demo_checks.cjs
PY ?= python3

SLOPCHECK ?= $(HOME)/Projects/coding-best-practices/tools/slopcheck.py

check: selftest index verify evals publication site-check prose   ## the full gate; reads captures kept out of the repository

check-public: selftest publication doctor   ## the gates a fresh clone satisfies; CI runs these
	$(PY) scripts/site_checks.py

selftest:                       ## every script's offline fixtures
	$(PY) scripts/portal_api.py --selftest
	$(PY) scripts/security_checks.py --selftest
	$(PY) scripts/mcp_checks.py --selftest
	$(PY) scripts/site_checks.py --selftest
	$(PY) scripts/build_site.py --selftest
	$(PY) scripts/watchdog.py --selftest
	$(PY) scripts/catalog_diff.py --selftest
	$(PY) scripts/verify_claims.py --selftest
	$(PY) scripts/fetch_sources.py --selftest
	$(PY) scripts/publication.py --selftest
	$(PY) scripts/mcp_evals.py --selftest
	$(PY) scripts/build_folder_index.py --selftest
	$(PY) scripts/record_mcp_examples.py --selftest

prose:                          ## AI-register and rhythm gate over authored copy; scope in .slopcheck.json
	@if [ -f "$(SLOPCHECK)" ]; then $(PY) "$(SLOPCHECK)" --fail-on WARN .; \
	else echo "prose: SKIPPED, $(SLOPCHECK) not found (coding-best-practices/tools); see design.md, Content rules"; fi

evals:                          ## golden anchors against the local captures (offline)
	$(PY) scripts/mcp_evals.py

publication:                    ## every published file is allowlisted and intact
	$(PY) scripts/publication.py check

folders:                        ## rebuild docs/data/folders.json from the accepted snapshot
	$(PY) scripts/build_folder_index.py

examples:                       ## re-record docs/data/mcp-examples.json from the local server
	$(PY) scripts/record_mcp_examples.py

doctor:                         ## check the local evidence and the server, no network
	$(PY) scripts/sept11_mcp.py doctor

index:                          ## regenerate research/INDEX.md and fail on unsourced notes
	$(PY) scripts/build_research_index.py --check

verify:                         ## offline claim verification against captured evidence
	$(PY) scripts/verify_claims.py research/claims/claims.json --md research/claims/verification-latest.md --strict

claims-online:                  ## also probe every claim URL (rate-limited)
	$(PY) scripts/verify_claims.py research/claims/claims.json --online --md research/claims/verification-latest.md

catalog:                        ## snapshot the portal catalog and diff against the previous one
	$(PY) scripts/export_catalog.py

watchdog:                       ## one export -> immutable snapshot -> accept/quarantine -> publish
	$(PY) scripts/watchdog.py run

mcp:                            ## run the read-only MCP server on stdio
	$(PY) scripts/sept11_mcp.py

mcp-tools:                      ## print the MCP tool catalog as JSON
	$(PY) scripts/sept11_mcp.py tools --json

sources:                        ## (re)capture primary sources listed in research/sources_manifest.json
	uv run --quiet --with pypdf --with cryptography $(PY) scripts/fetch_sources.py

site-check:                     ## structural gates for the pages, and their numbers against the data
	$(PY) scripts/site_checks.py
	$(PY) scripts/build_site.py --check
	$(PY) scripts/build_folder_index.py --check
	$(PY) scripts/record_mcp_examples.py --check

build-site:                     ## rewrite the page's generated regions from the reviewed data
	$(PY) scripts/build_site.py

site:                           ## local preview of the GitHub Pages site
	$(PY) -m http.server 8000 --directory docs
