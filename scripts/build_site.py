#!/usr/bin/env python3
"""build_site.py: put the site's numbers, demos and tool table under the data's control.

Hand-inlined figures drift: a new catalog snapshot changed `docs/data/`, and the
page kept yesterday's totals until someone remembered. This build writes the
marked regions of the five pages from the reviewed artifacts and the server's
own tool registry, and refuses to write a number the claim registry contradicts.

It serves only files on the publication allowlist (`review/publication.json`),
so a stray file under `research/` or `data/` cannot reach a public page. It
reads the claim registry and the captured sources for one purpose: to refuse
a quote the page would display that is not the registered quote, located in
its captured source.

    python3 scripts/build_site.py            # rewrite the generated regions
    python3 scripts/build_site.py --check    # fail if the page and the data disagree
    python3 scripts/build_site.py --selftest # offline fixtures
"""
from __future__ import annotations

import argparse
import hashlib
import html as markup
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sept11.core import calendar, chronology  # noqa: E402
from sept11.core import commitments as ledger  # noqa: E402
from sept11.core.text import quote_hits, split_pages  # noqa: E402
from sept11.mcp.tools import TOOLS  # noqa: E402
from sept11.storage.publication import Publication  # noqa: E402

DOCS = ROOT / "docs"
SITE_URL = "https://pranava0x0.github.io/sept11documents-mcp/"
CLAIMS = ROOT / "research" / "claims" / "claims.json"
TOOLS_OPEN, TOOLS_CLOSE = "<!-- BUILD:tools -->", "<!-- /BUILD:tools -->"

# The site is five pages, not one scroll. Order here is the order in the menu.
PAGES = [
    ("index.html", "Overview"),
    ("toolkit.html", "The MCP"),
    ("examples.html", "Examples"),
    ("records.html", "Records"),
    ("community.html", "Community support"),
]

MAST = """<header class="mast"><div class="in"><a class="word" href="index.html">September 11th Documents \
Toolkit</a><span class="note">Independent project. Not an official City of New York website. Not legal \
advice.</span></div></header>
<nav class="top" aria-label="Pages"><div class="in"><ul>
{items}
</ul></div></nav>"""

FOOTER = """<footer><div class="in">
<p>Independent research prototype, not affiliated with the City of New York or 9/11 Health Watch, and not \
legal advice. Records stay on the City's portal; this site links to them. Catalog snapshot \
{snapshot}. <a href="https://sept11documents.cityofnewyork.us/">Original City records</a> · \
<a href="llms.txt">For agents</a> · <a href="#top">Back to top ↑</a></p>
</div></footer>"""

# Rows a registry cannot supply, because the tool does not exist, with the reason a reader
# deserves. Kept here so the table states what the toolkit deliberately leaves out.
PLANNED_ROWS = [
    ("Exposure-zone check for an address",
     "Whether an address is inside a program's zone. Each program decides that; "
     "<code>building_lookup</code> quotes the definitions and links the official maps",
     "not planned · the programs decide"),
]

# What each tool returns, in the site's register. Derived text would read like a schema dump;
# a missing entry is an error, so a new tool cannot ship without a line a reader can use.
TOOL_BLURBS = {
    "portal_search": ("Live portal hits with Bates, collection, box, folder, inert snippet text, and a "
                      "precision warning for fuzzy queries. Off unless the operator opts in, because the "
                      "words searched reach the City"),
    "catalog_search": ("The same catalog searched locally by Bates, collection, agency, box or folder "
                       "label, every word matched, with facets by collection, box and folder over all "
                       "matches; nothing is sent to the City"),
    "portal_get_document": ("Metadata, page count, PDF link, the documents filed before and after it in the "
                            "same folder, and when it was first and last seen in local snapshots; no date "
                            "field, because the portal publishes none"),
    "portal_get_page_text": ("Verbatim page text with the Bates stamp printed on that page, read from the "
                             "page's own footer stamp; withheld whole when personal information is "
                             "suspected"),
    "portal_catalog_stats": "Totals by collection, agency and production volume, with the snapshot's SHA-256",
    "portal_browse": "The collection → box → folder tree, largest first, with counts",
    "portal_changes_since": ("Documents observed added, absent or changed between two accepted captures, "
                             "with the interval compared"),
    "citations_verify": ("found / not-found / no-claim / unverifiable for quotes at Bates pages or "
                         "registered sources; an ellipsis marks omitted words; it accepts no URLs and no "
                         "file paths"),
    "doi_milestones": ("The dated obligations with what was observed and when it was checked, with no score "
                       "and no legal determination"),
    "budget_lookup": ("The commitment ledger: each announced amount with its source and five separately "
                      "evidenced stages, unlinked stages named as such; rows are never summed"),
    "timeline_lookup": ("Public statements, City records and later reviews in date order, with quotes, "
                        "citation and the basis for its date; filter by side, topic or dates"),
    "readings_lookup": ("Sampling results printed in located City records: value, unit, medium and location as "
                        "printed, with the quote and Bates page; never converted or compared"),
    "building_lookup": ("Folders whose City label names an address or BIN, with the house numbers and "
                        "identifiers the label prints and the follow-up catalog call; zones are quoted, not decided"),
    "presence_evidence": ("What the WTC Health Program and the VCF accept as proof of presence, by program and "
                          "applicant group, every rule quoted from the official page"),
    "upcoming_dates": ("Dated settlement and Res. 560-A obligations with days from a given date, recurring "
                       "obligations listed without invented dates, and an iCalendar file"),
    "portal_query_draft": ("A portal search written in the syntax the City's search accepts, with the matching "
                           "catalog filters; the draft is not sent anywhere"),
    "citations_format": ("Short, full and Markdown citations for a Bates page, with the page link and whether the "
                         "printed stamp was read"),
    "records_manifest": ("A reading list for up to 50 Bates numbers: collection, box, folder, pages and PDF link, "
                         "as rows and as CSV, with any number the catalog lacks"),
}

# Chip labels for the publishers the pages quote, keyed by URL prefix; specific prefixes first.
PUBLISHERS = [
    ("https://www.cdc.gov/wtc/pdfs/statistics/", "CDC quarterly summary, 2026-06-30"),
    ("https://www.epa.gov/archive/epapages/newsroom_archive/newsreleases/ed368f43303656488525744e00039488.html",
     "EPA release, 2001-09-18"),
    ("https://www.epa.gov/archive/epapages/newsroom_archive/newsreleases/d7ada9cf2d39c0a185256acc007c097f.html",
     "EPA release, 2001-09-13"),
    ("https://www.epa.gov/archive/epapages/newsroom_archive/newsreleases/1dcfffa0c895910885256ace0082cdeb.html",
     "EPA release, 2001-09-21"),
    ("https://www.epa.gov/archive/epapages/newsroom_archive/newsreleases/a58e3e62ff609f5185256ada00729977.html",
     "EPA and OSHA release, 2001-10-03"),
    ("https://www.nyc.gov/html/om/html/2001b/pr315-01b.html", "Mayor’s Office release, 2001-09-16"),
    ("https://19january2021snapshot.epa.gov/sites/static/files/2015-10/documents/wtc_report_20030821.pdf",
     "EPA OIG report 2003-P-00012"),
    ("https://www.cdc.gov/wtc/documentation.html", "CDC, supporting documentation"),
    ("https://www.cdc.gov/wtc/about.html", "CDC, about the Program"),
    ("https://www.vcf.gov/policy/", "VCF Policies and Procedures"),
    ("https://www.911healthwatch.org/files/101ba5889feaae84c19c8f8.pdf", "Stipulation and Order"),
    ("https://www.nyc.gov/mayors-office/news/2026/09/transcript", "Transcript, 2026-09-08"),
    ("https://www.nyc.gov/mayors-office/", "Mayor’s Office release"),
    ("https://www.nyc.gov/assets/doi/downloads/Sept_11_Investigation/12_31_2025", "DOI letter, 2025-12-31"),
    ("https://www.nyc.gov/assets/doi/downloads/Sept_11_Investigation/06_29_2026", "DOI letter, 2026-06-29"),
    ("https://www.nyc.gov/assets/doi/Testimony/", "DOI testimony"),
    ("https://www.nyc.gov/site/doi/", "DOI, 9/11 investigation page"),
    ("https://legistar.council.nyc.gov/", "Res. 560-A"),
    ("https://council.nyc.gov/budget/", "Council Schedule C, FY2027"),
    ("https://council.nyc.gov/", "City Council release"),
    ("https://www.schools.nyc.gov/", "NYC Public Schools"),
    ("https://www.youtube.com/", "Mayor’s Office video"),
]

# The landing page quotes the City in its own words. Each entry is (lead, claim id); the
# quote itself comes from the claim record and is located in its captured source at build.
STATEMENTS = [
    ("Settlement and order · September 2026", [
        ("", "settlement-portal-goal"),
        ("the records sought under FOIL and", "settlement-records-of-public-interest"),
        ("Monthly meetings:", "settlement-monthly-meetings"),
        ("A privilege log,", "settlement-privilege-log"),
        ("The Harding memo", "settlement-documents-of-interest-tab"),
        ("Personnel:", "settlement-dcas-doe-personnel"),
        ("The DOI inquiry:", "settlement-doi-independence"),
        ("After twelve months the proceedings are", "settlement-dismissed-with-prejudice"),
    ]),
    ("Council Resolution 560-A, adopted July 14, 2025", [
        ("DOI is directed", "res-560a-purpose"),
        ("The Council acted", "res-560a-charter-803"),
        ("Written updates", "res-560a-update-dates"),
        ("The report must include", "res-560a-timeline"),
        ("and", "res-560a-contrast-analysis"),
    ]),
    ("The announcement of September 8, 2026", [
        ("Corporation Counsel Steven Banks:", "banks-for-too-long"),
        ("On the 68 boxes:", "banks-right-to-know"),
        ("On the rollout:", "banks-rolling-releases"),
        ("For the agreed search terms the City will", "banks-search-terms"),
        ("Mayor Zohran Mamdani:", "mamdani-34-million-commitment"),
        ("On what is withheld:", "mamdani-omissions"),
        ("", "mamdani-nothing-else-redacted"),
        ("The Mayor’s Office release:", "mayor-release-updated-regularly"),
        ("and the administration will", "mayor-dcas-nycps-personnel"),
    ]),
    ("The Department of Investigation’s account", [
        ("December 31, 2025, DOI reported requesting", "doi-2025-12-request-over-3-million"),
        ("and", "doi-2025-12-not-yet-approved"),
        ("June 29, 2026:", "doi-2026-06-request-4-million"),
        ("OMB", "doi-2026-06-omb-not-approved"),
        ("DOI", "doi-2026-06-two-firms"),
        ("DOI’s investigation page:", "doi-page-3-81-million"),
        ("", "doi-page-procurement"),
        ("", "doi-page-public-report"),
    ]),
]

# The purpose section: public statements about the air, the records beside them, and the
# program figures, each a verified claim. A line is a list of segments (lead, claim id, kind,
# extra): a "quote" segment renders the registered quote in <q> with an optional note; a
# "figure" segment renders the registered number as text followed by the extra words.
PURPOSE = [
    [("September 18, 2001, EPA Administrator Christine Todd Whitman:", "epa-2001-09-18-safe-to-breathe",
      "quote", "as published")],
    [("August 21, 2003, EPA’s Office of Inspector General, on that statement:", "oig-2003-sept18-insufficient-data",
      "quote", None)],
    [("October 2001, memo to Deputy Mayor Robert Harding, among the potential lawsuits it lists:",
      "harding-health-advisories", "quote", None)],
    [("February 28, 2002, memo from Special Advisor Ester Fuchs, on the", "fuchs-real-health-risks", "quote", None)],
    [("As of June 30, 2026,", "wtchp-current-enrollment", "figure", "members were enrolled in the WTC Health Program")],
    [("As of the same date, lifetime cancer certifications in the Program totaled",
      "wtchp-cancer-certifications-total", "figure", None)],
]

STATUS_BADGE = {"observed": ("met", "observed"), "partially_observed": ("partial", "partially observed"),
                "not_observed": ("notvisible", "not observed on inspected surfaces"),
                "unable_to_check": ("notvisible", "unable to check"), "upcoming": ("upcoming", "upcoming"),
                "disputed": ("notvisible", "disputed")}
STAGE_STATE_LABEL = {"located": "located", "not_linked": "not linked", "observed": "observed",
                     "partially_observed": "partially observed", "not_observed": "not observed",
                     "unable_to_check": "unable to check", "upcoming": "upcoming"}

e = markup.escape


class Quotes:
    """The registered claims, and a refusal for any displayed quote that is not one of them.

    A quote reaches a page only when (1) it is exactly the registered quote of the claim
    it names and (2) that quote is located in the claim's captured source. A drifted
    quote or a missing capture stops the build.
    """

    def __init__(self, claims: list[dict], root: Path = ROOT, locate: bool = True):
        self.claims = {c["id"]: c for c in claims}
        self.root = root
        self.locate = locate
        self.checked = 0

    def claim(self, claim_id: str) -> dict:
        if claim_id not in self.claims:
            raise ValueError(f"no claim {claim_id!r} in research/claims/claims.json")
        return self.claims[claim_id]

    def url(self, claim_id: str) -> str:
        source = self.claim(claim_id)["source"]
        if source.get("type") == "portal":
            return pdf_link(source["bates"], int(source.get("page") or 1))
        url = source.get("url", "")
        if not url.startswith("https://"):
            raise ValueError(f"claim {claim_id!r} has no HTTPS source URL")
        return url

    def verify(self, claim_id: str, text: str) -> str:
        claim = self.claim(claim_id)
        if text != claim.get("quote"):
            raise ValueError(f"page text for {claim_id!r} is not the registered quote: {text!r}")
        source = claim["source"]
        if self.locate:
            if source.get("type") == "portal":
                local = f"research/raw/sources/portal/{source['bates']}.txt"
            else:
                local = source.get("local_text")
                if source.get("type") != "url" or not local:
                    raise ValueError(f"claim {claim_id!r} is not a captured URL or portal source; pages quote only those")
            path = self.root / local
            if not path.is_file():
                raise ValueError(f"claim {claim_id!r}: captured source missing at {local}")
            pages = split_pages(path.read_text(encoding="utf-8", errors="replace"))
            targets = [source["page"]] if source.get("page") else sorted(pages)
            if not any(quote_hits(pages.get(n, ""), text, claim.get("variants")) for n in targets):
                raise ValueError(f"claim {claim_id!r}: quote not located in {local}")
        self.checked += 1
        return text

    def chip(self, claim_id: str, label: str | None = None) -> str:
        url = self.url(claim_id)
        source = self.claim(claim_id)["source"]
        if source.get("type") == "portal":
            label = label or f"{source['bates']} · p.{int(source.get('page') or 1)}"
            return f'<a class="cite" href="{e(url, quote=True)}">{e(label)}</a>'
        if label is None:
            label = next((name for prefix, name in PUBLISHERS if url.startswith(prefix)), None)
            if label is None:
                raise ValueError(f"no publisher label for {url}")
        page = self.claim(claim_id)["source"].get("page")
        if page:
            label = f"{label}, p.{page}"
        return f'<a class="cite" href="{e(url, quote=True)}">{e(label)}</a>'

    def quoted(self, item: dict) -> str:
        """A directory item: optional lead, the verified quote, its chip, an optional note."""
        text = self.verify(item["claim_id"], item["text"])
        lead = f"{e(item['lead'])} " if item.get("lead") else ""
        note = f' <span class="muted">({e(item["note"])})</span>' if item.get("note") else ""
        return f"{lead}<q>{e(text)}</q> {self.chip(item['claim_id'])}{note}"


def metrics_from(summary: dict) -> dict[str, str]:
    """The figures the page shows, formatted as the page shows them."""
    return {
        "documents": f"{summary['documents']:,}",
        "pages": f"{summary['pages']:,}",
        # Decimal GB, one place, as the City's own transcript reports size.
        "pdf_bytes": f"{summary['pdf_bytes'] / 1_000_000_000:.1f} GB",
        "collections": f"{len(summary['by_source']):,}",
        "folders": f"{summary['folders']:,}",
    }


def claim_conflicts(summary: dict, claims: list[dict]) -> list[str]:
    """A published number that contradicts its claim record is a defect, not a rounding."""
    problems = []
    for claim in claims:
        expected = claim.get("expected") or {}
        for key, value in expected.items():
            if key in summary and summary[key] != value:
                problems.append(f"{claim['id']} expects {key}={value}, the summary says {summary[key]}")
    return problems


def tool_rows() -> str:
    rows = []
    for tool in TOOLS.values():
        if tool.name not in TOOL_BLURBS:
            raise SystemExit(f"build_site: no site copy for tool {tool.name!r}; add it to TOOL_BLURBS")
        status = "runs from a clone" + (" · live opt-in" if tool.open_world else "")
        rows.append((f"<code>{tool.name}</code>", TOOL_BLURBS[tool.name],
                     f'<span class="badge built">{status}</span>'))
    for name, returns, status in PLANNED_ROWS:
        rows.append((name, returns, f'<span class="badge">{status}</span>'))
    return "\n".join(
        f'<tr><td data-label="Tool">{name}</td><td data-label="Returns">{returns}</td>'
        f'<td data-label="Availability">{status}</td></tr>' for name, returns, status in rows)


def asset_version(name: str) -> str:
    """Eight hex characters of the file's own hash, used as a cache-busting query."""
    return hashlib.sha256((DOCS / "assets" / name).read_bytes()).hexdigest()[:8]


def version_assets(html: str) -> str:
    for name in ("site.css", "site.js"):
        html = re.sub(rf'(assets/{re.escape(name)})(\?v=[0-9a-f]+)?', rf'\1?v={asset_version(name)}', html)
    return html


def mast_for(page: str) -> str:
    """One menu, written into every page, with the current page marked."""
    items = "".join(
        f'<li><a href="{href}"' + (' aria-current="page"' if href == page else "") + f'>{label}</a></li>'
        for href, label in PAGES)
    return MAST.format(items=items)


def region(html: str, name: str, content: str) -> str:
    """Replace one <!-- BUILD:name --> … <!-- /BUILD:name --> region."""
    open_tag, close_tag = f"<!-- BUILD:{name} -->", f"<!-- /BUILD:{name} -->"
    if open_tag not in html:
        return html
    start, end = html.index(open_tag), html.index(close_tag)
    return html[:start] + open_tag + "\n" + content + "\n" + html[end:]


def pdf_link(bates: str, page: int) -> str:
    if not re.fullmatch(r"NYC-WTC_[0-9]+", bates) or type(page) is not int or page < 1:
        raise ValueError("invalid demo citation")
    return f"https://sept11documents.cityofnewyork.us/apps/content/September11_MD/{bates}.pdf#page={page}"


def record_demo(anchors: dict) -> str:
    """Small, explicit selection from the allowlisted research anchors; no raw-file scan."""
    chosen = [("harding-memo", "Harding memo"),
              ("clean-up-initiative", "Clean Up Initiative"),
              ("15-john-street", "15 John Street lab report")]
    indexed = {row["id"]: row for row in anchors["anchors"]}
    options, articles = [], []
    for key, title in chosen:
        row = indexed[key]
        bates, page = row["bates"], row["page"]
        url = pdf_link(bates, page)
        options.append(f'<option value="{key}">{e(title)}</option>')
        articles.append(f'''<article class="record-result" data-record="{key}">
<p class="eyebrow">{e(row['collection'])} · research example</p>
<h3>{e(title)}</h3>
<blockquote>{e(row['quote'])}</blockquote>
<div class="result-actions"><a class="btn" href="{url}">Open source · p.{page}</a>
<button type="button" class="quiet copy-citation" data-copy="{bates} p.{page} — {url}" hidden>Copy citation</button></div>
<p class="source-id"><a href="{url}">{bates} · p.{page}</a></p>
</article>''')
    return ('<label for="record-choice">Choose a record</label>\n'
            '<select id="record-choice">' + ''.join(options) + '</select>\n' + '\n'.join(articles))


def help_demo(directory: dict, quotes: Quotes, scorecard: dict) -> str:
    """The official directory: programs, then the New York City issuers, every rule a verified quote."""
    options, articles = [], []
    for program in directory["programs"]:
        pid = program["id"]
        options.append(f'<option value="{pid}">{e(program["name"])}</option>')
        facts = []
        contact = program["contact"]
        facts.append(("Contact", f'<a href="{e(contact["url"], quote=True)}">{e(contact["url"].replace("https://", "").rstrip("/"))}</a>'
                      f' · {e(contact["phone"])} {quotes.chip(contact["claim_id"])}'))
        if program.get("what_it_is"):
            facts.append(("Provides", quotes.quoted(program["what_it_is"])))
        deadline = program["deadline"]
        line = quotes.quoted(deadline)
        if deadline.get("caveat"):
            line += " " + quotes.quoted(deadline["caveat"])
        facts.append(("Deadline", line))
        window = program["window"]
        facts.append(("Time window", quotes.quoted(window) + f' <span class="muted">{e(window["applies_to"])}</span>'))
        zone = program["zone"]
        if zone.get("definition"):
            parts = [quotes.quoted(item) for item in zone["definition"]]
            zone_line = f'{e(zone["name"])}: ' + " ".join(parts)
            if zone.get("also"):
                zone_line += " " + quotes.quoted(zone["also"])
            zone_line += f' <a href="{e(zone["map_url"], quote=True)}">Map ↗</a>'
        else:
            zone_line = (f'{e(zone["name"])}, <a href="{e(zone["definition_url"], quote=True)}">defined on the CDC page ↗</a>'
                         f' <span class="muted">{e(zone["note"])}</span>')
        facts.append(("Zone", zone_line))
        facts.append(("Separate program", quotes.quoted(program["separate_from"])))
        facts_html = "".join(f"<div><dt>{e(k)}</dt><dd>{v}</dd></div>" for k, v in facts)
        rules_html = "".join(f"<li>{quotes.quoted(rule)}</li>" for rule in program.get("rules", []))
        lanes = []
        for lane in program["evidence"]:
            items = []
            for item in lane["examples"]:
                who = f' <span class="muted">{e(item["who"])}</span>' if item.get("who") else ""
                extra = (f' <a href="{e(item["contact_url"], quote=True)}">{e(item["note"])}</a>'
                         if item.get("contact_url") else "")
                shown = dict(item)
                if item.get("contact_url"):
                    shown.pop("note", None)
                items.append(f"<li>{quotes.quoted(shown)}{extra}{who}</li>")
            lanes.append(f'<li><b>{e(lane["lane"])}</b><ul>{"".join(items)}</ul></li>')
        pages = " · ".join(f'<a href="{e(p["url"], quote=True)}">{e(p["label"])}</a>' for p in program["official_pages"])
        articles.append(f'''<article class="help-result" data-help="{pid}">
<p class="eyebrow">{e(program["administered_by"])}</p>
<h3>{e(program["name"])}</h3>
<dl class="facts">{facts_html}</dl>
<h4>Rules the program states</h4>
<ul class="rules">{rules_html}</ul>
<h4>Records that prove presence</h4>
<ul class="lanes">{"".join(lanes)}</ul>
<p class="official">Official pages: {pages}</p>
</article>''')
    statuses = {row["id"]: row for row in scorecard["rows"]}
    rows = []
    for issuer in directory["nyc_records"]:
        basis = ""
        if issuer.get("commitment"):
            basis += quotes.quoted(issuer["commitment"])
        if issuer.get("note"):
            basis += (" " if basis else "") + quotes.quoted(issuer["note"])
        if issuer.get("route"):
            basis += "".join(" " + quotes.quoted(item) for item in issuer["route"])
        if issuer.get("contact"):
            basis += f' <a href="mailto:{e(issuer["contact"], quote=True)}">{e(issuer["contact"])}</a>'
        if issuer.get("status_obligation_id"):
            row = statuses[issuer["status_obligation_id"]]
            css, label = STATUS_BADGE[row["status"]]
            basis += f' <span class="badge {css}">{e(label)}</span>'
        basis += f'<span class="cellnote">{e(issuer["how"])}</span>'
        rows.append(f'<tr><td data-label="Applicant">{e(issuer["who"])}</td><td data-label="Issuer">{e(issuer["issuer"])}</td>'
                    f'<td data-label="Basis and status">{basis}</td></tr>')
    mayor = directory["mayor_announcement"]
    announcement = (f'<p class="result-note">The Mayor’s Office release says the administration will '
                    f'<q>{e(quotes.verify(mayor["claim_id"], mayor["text"]))}</q>. '
                    f'{quotes.chip(mayor["claim_id"])}</p>')
    table = ('<div class="tw"><table>\n<colgroup><col class="w30"><col class="w30"><col class="w40"></colgroup>\n'
             '<thead><tr><th>Applicant</th><th>Issuer</th><th>Basis and status</th></tr></thead>\n<tbody>\n'
             + "\n".join(rows) + '\n</tbody></table></div>')
    return ('<label for="help-choice">Choose a program</label>\n'
            '<select id="help-choice">' + "".join(options) + '</select>\n' + "\n".join(articles)
            + '\n<h3>New York City issuers of records</h3>\n' + table + "\n" + announcement
            + f'\n<p class="result-note">{e(directory["disclaimer"])}</p>')


def budget_demo(document: dict, quotes: Quotes) -> str:
    """The commitment ledger: one article per row, five stages, notes from the data, never a total."""
    ledger.validate(document)
    labels = document["stage_labels"]
    options, articles = [], []
    for row in document["commitments"]:
        claim = quotes.claim(row["claim_id"])
        if row["amount_display"] not in claim["quote"] and row["amount_display"] not in " ".join(claim.get("variants") or []):
            raise ValueError(f"{row['id']}: amount {row['amount_display']!r} is not in the quote of {row['claim_id']!r}")
        period = ", ".join(row["fiscal_periods"]).replace("FY", "FY ") if "unresolved" not in row["fiscal_periods"] else "fiscal period unresolved"
        options.append(f'<option value="{e(row["topic"])}">{e(row.get("short") or row["title"])} · {e(row["amount_display"])}</option>')
        announcement = row["stages"]["announcement"]
        if announcement["url"] != quotes.url(row["claim_id"]):
            raise ValueError(f"{row['id']}: announcement URL differs from the claim's source")
        buttons, notes = [], []
        for stage in document["stages"]:
            spec = row["stages"][stage]
            state = STAGE_STATE_LABEL[spec["state"]]
            pressed = "true" if stage == "announcement" else "false"
            buttons.append(f'<button type="button" data-stage="{stage}" aria-pressed="{pressed}">{e(labels[stage])}'
                           f'<span class="state">{e(state)}</span></button>')
            body = e(spec["note"])
            if spec.get("next"):
                body += f' <span class="next">Next: {e(spec["next"])}</span>'
            if spec.get("obligation_ids"):
                body += ' <a href="index.html#obligations">Dated obligations ↗</a>'
            notes.append(f'<div data-stage="{stage}"><dt>{e(spec["title"])}</dt><dd>{body}</dd></div>')
        questions = "".join(f"<li>{e(q)}</li>" for q in row["questions"])
        observations = ""
        if row.get("observations"):
            items = []
            for obs in row["observations"]:
                lead = f"{e(obs['lead'])} " if obs.get("lead") else ""
                items.append(f'<li><b>{e(obs["date"])}</b> {lead}<q>{e(quotes.verify(obs["claim_id"], obs["text"]))}</q> '
                             f'{quotes.chip(obs["claim_id"], obs.get("label"))}</li>')
            observations = '<h4>Also on record</h4><ul class="statements">' + "".join(items) + "</ul>"
        articles.append(f'''<article class="budget-result" data-budget="{e(row["topic"])}" data-claim="{e(row["claim_id"])}">
<p class="eyebrow">{e(period)} · announced commitment</p><h3>{e(row["title"])}</h3>
<p class="amount">{e(row["amount_display"])}</p><p>{e(row["description"])}</p>
<a class="source-link" href="{e(announcement["url"], quote=True)}">{e(announcement["label"])} ↗</a>
{('<p class="result-note">' + e(row["period_note"]) + '</p>') if row.get("period_note") else ''}
<h4>Evidence for each stage</h4>
<div class="evidence-stages" role="group" aria-label="Evidence stages">{"".join(buttons)}</div>
<dl class="stage-notes">{"".join(notes)}</dl>
{observations}
<h4>Open questions</h4>
<ul class="plain">{questions}</ul>
</article>''')
    return ('<label for="budget-choice">Choose a commitment</label>\n'
            '<select id="budget-choice">' + "".join(options) + '</select>\n' + "\n".join(articles))


def current_context_demo(document: dict) -> str:
    """Small, dated reading list; links distinguish official records from outside projects."""
    items = []
    for row in document["items"]:
        items.append(f'''<article class="context-item">
<p class="eyebrow">{e(row["date"])} · {e(row["kind"])}</p><h3>{e(row["title"])}</h3>
<p>{e(row["summary"])}</p><a class="source-link" href="{e(row["source_url"], quote=True)}">{e(row["source_label"])} ↗</a>
<p class="result-note"><b>How to use it:</b> {e(row["use"])}</p></article>''')
    return (f'<p class="result-note">As of {e(document["as_of"])} · {e(document["window"])}. '
            f'{e(document["scope_note"])}</p>' + "\n".join(items))


def research_paths_demo(document: dict) -> str:
    """Question-led routes make the MCP’s boundaries as visible as its capabilities."""
    options, articles = [], []
    for row in document["paths"]:
        options.append(f'<option value="{e(row["id"])}">{e(row["question"])}</option>')
        articles.append(f'''<article class="research-path" data-path="{e(row["id"])}">
<h3>{e(row["title"])}</h3><p class="tool-path"><code>{e(row["tools"])}</code></p>
<p>{e(row["answer"])}</p><p class="result-note"><b>Boundary:</b> {e(row["boundary"])}</p></article>''')
    return ('<label for="path-choice">Choose a research question</label>\n'
            '<select id="path-choice">' + "".join(options) + '</select>\n' + "\n".join(articles))


def release_footprint_demo(summary: dict) -> str:
    """Show the saved City catalog as a bounded snapshot, not a completeness claim."""
    total = summary["documents"]
    rows = []
    for source, count in sorted(summary["by_source"].items(), key=lambda pair: -pair[1]):
        share = f"{count / total:.1%}"
        rows.append(f'<tr><td data-label="Collection">{e(source)}</td><td data-label="Documents">{count:,}</td>'
                    f'<td data-label="Share of saved catalog">{share}</td></tr>')
    return f'''<div class="release-metrics" aria-label="Saved catalog footprint">
<div><b>{total:,}</b><span>documents</span></div><div><b>{summary["pages"]:,}</b><span>pages</span></div>
<div><b>{summary["boxes"]:,}</b><span>boxes</span></div><div><b>{summary["folders"]:,}</b><span>folder labels</span></div></div>
<p class="result-note">Catalog captured {e(str(summary["captured_at"])[:10])}.</p>
<div class="tw"><table><colgroup><col class="w50"><col class="w25"><col class="w25"></colgroup>
<thead><tr><th>Collection</th><th>Documents</th><th>Share of saved catalog</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'''


def release_tracker_demo(scorecard: dict) -> str:
    """Release-specific settlement rows, with observed status distinct from compliance."""
    chosen = ("portal-launch", "harding-documents-of-interest", "search-terms-posted", "privilege-log",
              "dcas-doe-personnel")
    indexed = {row["id"]: row for row in scorecard["rows"]}
    options, articles = [], []
    for key in chosen:
        row = indexed[key]
        css, label = STATUS_BADGE[row["status"]]
        options.append(f'<option value="{e(key)}">{e(row["obligation"])}</option>')
        articles.append(f'''<article class="release-result" data-release="{e(key)}">
<p class="eyebrow">Checked {e(row["checked"])} · <span class="badge {css}">{e(label)}</span></p>
<h3>{e(row["obligation"])}</h3><p>{e(row["evidence"])}</p>
<p class="result-note"><b>Source:</b> {e(row["source"])}. Observation only.</p>
</article>''')
    return ('<label for="release-choice">Choose a release commitment</label>\n'
            '<select id="release-choice">' + "".join(options) + '</select>\n' + "\n".join(articles))


def collection_explorer_demo(summary: dict) -> str:
    """Collection filters a reader can paste into the local MCP without sending a query upstream."""
    agencies = summary["by_agency"]
    details = {
        "DEP Hard Copies (68 Boxes)": ("Environmental Protection, Dept. of", "DEP hard-copy production"),
        "WTC 7": ("Citywide Administrative Services, Dept. of", "WTC 7 collection label"),
        "DORIS Giuliani": ("Records and Information Services, Dept. of", "DORIS Giuliani collection label"),
    }
    options, articles = [], []
    for source, count in sorted(summary["by_source"].items(), key=lambda pair: -pair[1]):
        detail = details.get(source)
        if detail:
            agency, note = detail
            agency_context = (f'The summary reports {agencies.get(agency, 0):,} documents for {e(agency)} '
                              'across the saved catalog; it is not a one-to-one collection total.')
        else:
            note = "City collection label; no curated agency pairing is stored yet"
            agency_context = "Inspect the returned catalog rows for their agency field before drawing a collection-level conclusion."
        options.append(f'<option value="{e(source)}">{e(source)} · {count:,} documents</option>')
        request = json.dumps({"source": source, "count": 10}, ensure_ascii=False)
        articles.append(f'''<article class="collection-result" data-collection="{e(source)}">
<p class="eyebrow">{count:,} documents in saved catalog</p><h3>{e(source)}</h3>
<p>{e(note)}. {agency_context}</p>
<pre class="request">catalog_search {e(request)}</pre>
<p class="result-note">Local metadata only. Use a page tool for page text.</p></article>''')
    return ('<label for="collection-choice">Choose a City collection</label>\n'
            '<select id="collection-choice">' + "".join(options) + '</select>\n' + "\n".join(articles))


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
SIDE_LABELS = {"public_statement": "Public statement", "city_record": "City record", "later_review": "Later review"}


def display_date(value: str) -> str:
    """`2001-10-03` → October 3, 2001; `2001-10` → October 2001; a year stays a year."""
    chronology.check_date(value, "date")
    parts = value.split("-")
    if len(parts) == 1:
        return parts[0]
    month = MONTHS[int(parts[1]) - 1]
    return f"{month} {int(parts[2])}, {parts[0]}" if len(parts) == 3 else f"{month} {parts[0]}"


def recorded_for(recorded: dict, demo: str) -> list[dict]:
    rows = [ex for ex in recorded["examples"] if ex.get("demo") == demo and not ex["is_error"]]
    if not rows:
        raise ValueError(f"no recorded example for the {demo!r} demo; run scripts/record_mcp_examples.py")
    return rows


def recorded_note(ex: dict) -> str:
    """The line under a demo built from a recorded call: the call itself and a link to its envelope."""
    request = ex["request"]
    return (f'<p class="result-note">Recorded output of <code>{e(request["name"])}</code> '
            f'<code>{e(json.dumps(request["arguments"], ensure_ascii=False))}</code>. '
            f'<a href="toolkit.html#example-{e(ex["id"], quote=True)}">Full response ↗</a></p>')


def statements_timeline_demo(document: dict, quotes: Quotes) -> str:
    """Statements and records in date order. Every quote, and every date basis that cites a
    record, links to its own source; a month or a year means the source gives no closer date."""
    chronology.validate(document, "timeline")
    side_options = '<option value="all">All entries</option>' + "".join(
        f'<option value="{e(key)}">{e(label)}</option>' for key, label in document["sides"].items())
    topic_options = '<option value="all">All topics</option>' + "".join(
        f'<option value="{e(t)}">{e(t.capitalize())}</option>' for t in document["topics"])
    items = []
    for row in document["events"]:
        text = quotes.verify(row["claim_id"], row["quote"])
        basis = row["date_basis"]
        basis_html = e(basis["text"])
        if basis.get("claim_id"):
            basis_html += " " + quotes.chip(basis["claim_id"])
        elif basis.get("citation"):
            cited = basis["citation"]
            if cited["type"] == "portal":
                basis_html += (f' <a class="cite" href="{pdf_link(cited["bates"], cited["page"])}">'
                               f'{e(cited["bates"])} · p.{cited["page"]}</a>')
            else:
                basis_html += f' <a class="cite" href="{e(cited["url"], quote=True)}">source</a>'
        note = f'<p class="muted">{e(row["note"])}</p>' if row.get("note") else ""
        items.append(
            f'<li data-side="{e(row["side"])}" data-topic="{e(row["topic"])}">\n'
            f'<p class="eyebrow"><time datetime="{e(row["date"])}">{e(display_date(row["date"]))}</time> · '
            f'{e(SIDE_LABELS[row["side"]])} · {e(row["topic"])}</p>\n'
            f'<h3>{e(row["title"])}</h3>\n'
            f'<p><span class="muted">{e(row["source_label"])}:</span> <q>{e(text)}</q> {quotes.chip(row["claim_id"])}</p>\n'
            f'{note}<p class="date-basis"><b>Date basis:</b> {basis_html}</p></li>')
    return (f'<p class="result-note">{e(document["note"])}</p>\n'
            '<div class="filters"><div><label for="timeline-side">Show</label>'
            f'<select id="timeline-side">{side_options}</select></div>'
            '<div><label for="timeline-topic">Topic</label>'
            f'<select id="timeline-topic">{topic_options}</select></div></div>\n'
            '<p id="timeline-count" class="result-note" role="status"></p>\n'
            '<ol class="statement-timeline">' + "\n".join(items) + '</ol>\n'
            f'<p class="result-note"><b>Boundary:</b> {e(document["boundary"])}</p>')


def readings_demo(document: dict, quotes: Quotes) -> str:
    """Sampling results as printed: no conversion, no comparison, no total."""
    chronology.validate(document, "readings")
    rows = []
    for row in document["readings"]:
        text = quotes.verify(row["claim_id"], row["quote"])
        unit = f' {e(row["unit_as_printed"])}' if row["unit_as_printed"] else ""
        note = f'<span class="cellnote">{e(row["note"])}</span>' if row.get("note") else ""
        rows.append(
            f'<tr><td data-label="Result as printed"><b>{e(row["value_as_printed"])}{unit}</b>'
            f'<span class="cellnote">{e(row["analyte"])}, {e(row["medium"])}</span></td>'
            f'<td data-label="Location as printed">{e(row["location_as_printed"])}</td>'
            f'<td data-label="Sampled">{e(display_date(row["sample_date"]))}'
            f'<span class="cellnote">{e(row["sample_date_basis"])}</span></td>'
            f'<td data-label="Source"><q>{e(text)}</q> {quotes.chip(row["claim_id"])}'
            f'<span class="cellnote">{e(row["document"])}, {e(display_date(row["document_date"]))}; '
            f'{e(row["reported_by"])}</span>{note}</td></tr>')
    return (f'<p class="result-note">{e(document["note"])}</p>\n'
            '<div class="tw"><table class="readings">\n'
            '<colgroup><col class="w20"><col class="w25"><col class="w20"><col class="w35"></colgroup>\n'
            '<thead><tr><th>Result as printed</th><th>Location as printed</th><th>Sampled</th><th>Source</th></tr></thead>\n'
            '<tbody>\n' + "\n".join(rows) + '\n</tbody></table></div>\n'
            f'<p class="result-note"><b>Boundary:</b> {e(document["boundary"])}</p>')


def key_dates_demo(scorecard: dict) -> str:
    """Dated obligations in order, recurring ones apart, and the same dates as a calendar file.
    The page carries no day count; the reader's browser adds one against the reader's own date."""
    plan = calendar.schedule(scorecard, calendar.parse_day(str(scorecard["generated_at"])[:10], "generated_at"))
    items = []
    for row in plan["dated"]:
        css, label = STATUS_BADGE[row["status"]]
        items.append(f'<li><b><time datetime="{e(row["date"])}">{e(display_date(row["date"]))}</time></b> '
                     f'<span>{e(row["obligation"])}</span> <span class="badge {css}">{e(label)}</span> '
                     f'<span class="days" data-date="{e(row["date"])}"></span>'
                     f'<span class="cellnote">{e(row["source"])} · checked {e(row["checked"] or "not recorded")}</span></li>')
    rolling = "".join(f'<li><b>{e(row["due_as_stated"])}</b> <span>{e(row["obligation"])}</span>'
                      f'<span class="cellnote">{e(row["source"])}</span></li>' for row in plan["rolling"])
    return ('<ol class="plain due-list">' + "".join(items) + '</ol>\n'
            '<h3>Recurring obligations</h3>\n'
            f'<ul class="plain due-list">{rolling}</ul>\n'
            '<p class="result-note">Recurring obligations state no dates, so none are added to the calendar. '
            'Status is what was observed on public pages when checked, with no legal determination.</p>\n'
            '<p class="result-actions"><a class="btn" href="data/obligations.ics" download>Add the dated obligations '
            'to a calendar (.ics)</a></p>')


def address_demo(recorded: dict) -> str:
    """Recorded building_lookup calls, one per address: the folders whose labels name it."""
    options, articles = [], []
    for ex in recorded_for(recorded, "address"):
        data = ex["response"]["data"]
        options.append(f'<option value="{e(ex["id"])}">{e(ex["request"]["arguments"]["address"])}</option>')
        rows = []
        for folder in data["folders"]:
            ids = folder["printed_identifiers"]
            printed = ", ".join(x for x in (
                f'BIN {", ".join(ids["bins"])}' if ids["bins"] else "",
                f'block {ids["block"]}' if ids["block"] else "",
                f'lot {ids["lot"]}' if ids["lot"] else "") if x)
            rows.append(f'<tr><td data-label="Folder label">{e(folder["folder"])}'
                        f'<span class="cellnote">{e(folder["source"])} · {e(folder["box"])}</span></td>'
                        f'<td data-label="Documents">{folder["documents"]:,}</td>'
                        f'<td data-label="Printed on the label">{e(printed) or "no identifiers"}</td>'
                        f'<td data-label="First document"><a class="cite" href="{e(folder["first_pdf_url"], quote=True)}">'
                        f'{e(folder["first_bates"])}</a></td></tr>')
        if rows:
            table = ('<div class="tw"><table><colgroup><col class="w40"><col class="w15"><col class="w20">'
                     '<col class="w25"></colgroup><thead><tr><th>Folder label</th><th>Documents</th>'
                     '<th>Printed on the label</th><th>First document</th></tr></thead><tbody>'
                     + "".join(rows) + '</tbody></table></div>')
        else:
            table = '<p>No folder label in the capture names this address.</p>'
        more = (f'<p class="result-note">Showing {data["folders_returned"]} of {data["folders_matched"]} matching '
                'folders, largest first.</p>') if data["folders_returned"] < data["folders_matched"] else ""
        noun = "folder" if data["folders_matched"] == 1 else "folders"
        articles.append(
            f'<article class="address-result" data-address="{e(ex["id"])}">\n'
            f'<p class="eyebrow">Read as {e(data["query"]["read_as"])} · catalog captured '
            f'{e(data["catalog_captured_at"][:10])}</p>\n'
            f'<h3>{data["folders_matched"]:,} {noun}, {data["documents_in_matched_folders"]:,} documents</h3>\n'
            f'{table}{more}\n{recorded_note(ex)}</article>')
    return ('<label for="address-choice">Choose an address</label>\n'
            '<select id="address-choice">' + "".join(options) + '</select>\n' + "\n".join(articles)
            + '\n<p class="result-note">A label match shows where the City filed paper. A document can concern a '
              'building whose address is not on its folder. The lookup does not decide whether an address is '
              'inside a program’s exposure zone; each program decides that, and the response quotes its '
              'definition.</p>')


def cite_demo(recorded: dict) -> str:
    """Recorded citations_format calls: three forms of one page citation, each with a copy button."""
    options, articles = [], []
    for ex in recorded_for(recorded, "cite"):
        data = ex["response"]["data"]
        label = f'{data["bates"]} p.{data["page"]}'
        options.append(f'<option value="{e(ex["id"])}">{e(label)}</option>')
        forms = []
        for key, name in (("short", "Short"), ("full", "Full"), ("markdown", "Markdown")):
            text = data["formats"][key]
            forms.append(f'<div><dt>{name}</dt><dd><code class="citation-text">{e(text)}</code> '
                         f'<button type="button" class="quiet copy-citation" data-status="cite-status" '
                         f'data-copy="{e(text, quote=True)}" hidden>Copy</button></dd></div>')
        if data["stamp_status"] == "matched":
            stamp = "The Bates stamp printed on this page was read from the captured page text."
        elif data["stamp_status"] == "ambiguous_stamp":
            stamp = "The page text shows more than one Bates stamp, so none was chosen; the citation uses the catalog number."
        elif data.get("page_text_read"):
            stamp = "The page text was read and no Bates stamp was legible; the citation uses the catalog number."
        else:
            stamp = "The page text was not read, so the printed stamp was not checked."
        catalog = data.get("catalog") or {}
        place = " · ".join(x for x in (catalog.get("source"), catalog.get("box"), catalog.get("folder")) if x)
        articles.append(
            f'<article class="cite-result" data-cite="{e(ex["id"])}">\n'
            f'<p class="eyebrow">{e(place or "No catalog row")}</p>\n'
            f'<h3><a href="{e(data["pdf_url"], quote=True)}">{e(label)} ↗</a></h3>\n'
            f'<dl class="facts">{"".join(forms)}</dl>\n'
            f'<p class="result-note">{e(stamp)}</p>\n{recorded_note(ex)}</article>')
    return ('<label for="cite-choice">Choose a page</label>\n'
            '<select id="cite-choice">' + "".join(options) + '</select>\n' + "\n".join(articles)
            + '\n<p id="cite-status" class="result-note copy-status" role="status"></p>')


def manifest_demo(recorded: dict) -> tuple[str, str]:
    """The recorded records_manifest call as a table, and its CSV as a download."""
    ex = recorded_for(recorded, "manifest")[0]
    data = ex["response"]["data"]
    rows = []
    for row in data["records"]:
        place = " · ".join(x for x in (row["source"], row["box"], row["folder"]) if x)
        pages = "" if row["page_count"] is None else f'{row["page_count"]:,}'
        size = "" if row["pdf_size"] is None else f'{row["pdf_size"] / 1_000_000:.1f} MB'
        rows.append(f'<tr><td data-label="Bates"><a class="cite" href="{e(row["pdf_url"], quote=True)}">'
                    f'{e(row["bates"])}</a></td>'
                    f'<td data-label="Collection, box, folder">{e(place)}</td>'
                    f'<td data-label="Pages">{pages}</td><td data-label="PDF size">{size}</td></tr>')
    missing = (f'<p class="result-note">Not in the catalog: {e(", ".join(data["missing"]))}.</p>'
               if data["missing"] else "")
    html = (f'<p class="result-note">{data["found"]} of {data["requested"]} requested documents found in the '
            f'catalog captured {e(data["catalog_captured_at"][:10])}.</p>\n'
            '<div class="tw"><table><colgroup><col class="w25"><col class="w45"><col class="w15"><col class="w15">'
            '</colgroup><thead><tr><th>Bates</th><th>Collection, box, folder</th><th>Pages</th><th>PDF size</th>'
            '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>\n' + missing
            + '<p class="result-actions"><a class="btn" href="data/reading-list.csv" download>Download as CSV</a></p>\n'
            + recorded_note(ex))
    return html, data["csv"]


def statements_section(quotes: Quotes) -> str:
    """The City's commitments in its own words: verified quotes grouped by source."""
    groups = []
    for heading, items in STATEMENTS:
        lines = []
        for lead, claim_id in items:
            text = quotes.verify(claim_id, quotes.claim(claim_id)["quote"])
            lines.append(f'<li>{(e(lead) + " ") if lead else ""}<q>{e(text)}</q> {quotes.chip(claim_id)}</li>')
        groups.append(f'<h3>{e(heading)}</h3>\n<ul class="statements">{"".join(lines)}</ul>')
    return "\n".join(groups)


def _segment(quotes: Quotes, lead: str, claim_id: str, kind: str, extra: str | None) -> str:
    text = quotes.verify(claim_id, quotes.claim(claim_id)["quote"])
    head = f"{e(lead)} " if lead else ""
    if kind == "figure":
        tail = f" {e(extra)}" if extra else ""
        return f"{head}{e(text)}{tail} {quotes.chip(claim_id)}"
    note = f' <span class="muted">({e(extra)})</span>' if extra else ""
    return f"{head}<q>{e(text)}</q> {quotes.chip(claim_id)}{note}"


def purpose_section(quotes: Quotes, items: list | None = None) -> str:
    """The statements and records behind the purpose paragraph, every one a located claim."""
    lines = ["<li>" + " ".join(_segment(quotes, *segment) for segment in line) + "</li>"
             for line in (PURPOSE if items is None else items)]
    return '<ul class="statements">' + "".join(lines) + "</ul>"


def records_quote_problems(html: str, quotes: Quotes) -> tuple[list[str], int]:
    """Every blockquote on the records page holds a registered quote, located in its captured
    source, and the citation beside it links to that claim's source at the claim's page."""
    problems, matched = [], 0
    blocks = re.findall(r'<blockquote>(.*?)</blockquote>\s*<a class="cite" href="([^"]+)">', html, re.S)
    for body, href in blocks:
        shown = markup.unescape(re.sub(r"<[^>]+>", " ", body))
        hits = [cid for cid, claim in quotes.claims.items()
                if claim.get("quote") and quote_hits(shown, claim["quote"], claim.get("variants"))]
        located = []
        for cid in hits:
            try:
                quotes.verify(cid, quotes.claims[cid]["quote"])
            except ValueError:
                continue
            located.append(cid)
        if not located:
            problems.append(f"records: no located claim quote inside {shown.strip()[:60]!r}")
            continue
        link = markup.unescape(href)
        if link.split("#")[0] not in {quotes.url(cid).split("#")[0] for cid in located}:
            problems.append(f"records: {link} beside {shown.strip()[:40]!r} is not the source of {located}")
        pages = {quotes.url(cid) for cid in located if quotes.claims[cid]["source"].get("type") == "portal"}
        if pages and link not in pages:
            problems.append(f"records: {link} is not the page the claim cites ({sorted(pages)})")
        matched += 1
    return problems, matched


def money_ledger(document: dict, quotes: Quotes) -> str:
    """The announced commitments on the landing page, one tile each, never a total."""
    ledger.validate(document)
    tiles = []
    for row in document["commitments"]:
        claim = quotes.claim(row["claim_id"])
        if row["amount_display"] not in claim["quote"] and row["amount_display"] not in " ".join(claim.get("variants") or []):
            raise ValueError(f"{row['id']}: amount {row['amount_display']!r} is not in the quote of {row['claim_id']!r}")
        period = ", ".join(row["fiscal_periods"]).replace("FY", "FY ") if "unresolved" not in row["fiscal_periods"] else "fiscal period unresolved"
        announcement = row["stages"]["announcement"]
        tiles.append(f'<div><b>{e(row["amount_display"])}</b><span>{e(row.get("short") or row["title"])} · {e(period)} '
                     f'<a class="cite" href="{e(quotes.url(row["claim_id"]), quote=True)}">{e(announcement["label"])}</a></span></div>')
    return '<div class="ledger money" aria-label="Announced commitments">' + "".join(tiles) + "</div>"


def examples_section(recorded: dict) -> str:
    """Recorded exchanges for the builders page: one article per call, the envelope verbatim."""
    options, articles = [], []
    for ex in recorded["examples"]:
        request = ex["request"]
        options.append(f'<option value="{e(ex["id"])}">{e(request["name"])} · {e(ex["question"])}</option>')
        outcome = ("returned a structured error: " + e(ex["response"]["error"]["code"]) if ex["is_error"]
                   else "returned an envelope with warnings: "
                   + ", ".join(e(w["code"]) for w in ex["response"].get("warnings", [])))
        redactions = "".join(f'<p class="result-note">Published copy: {e(r)}.</p>' for r in ex.get("redactions", []))
        body = json.dumps(ex["response"], indent=2, ensure_ascii=False)
        articles.append(f'''<article class="example" id="example-{e(ex["id"])}" data-example="{e(ex["id"])}">
<h3>{e(ex["title"])}</h3>
<pre class="request">{e(request["name"])} {e(json.dumps(request["arguments"], ensure_ascii=False))}</pre>
<p class="result-note">The call {outcome}.</p>
{redactions}<pre class="response">{e(body)}</pre>
</article>''')
    return ('<label for="example-choice">Choose a call</label>\n'
            '<select id="example-choice">' + "".join(options) + '</select>\n'
            f'<p class="result-note">Recorded {e(recorded["recorded_at"][:10])} with server {e(recorded["server_version"])}, '
            f'schema {e(recorded["schema_version"])}; live search off; no network calls.</p>\n'
            + "\n".join(articles))


def render(html: str, metrics: dict[str, str], page: str = "index.html",
           snapshot: str = "catalog_2026-09-09_pdf.csv") -> str:
    def swap(match: re.Match) -> str:
        metric = match.group(1)
        if metric not in metrics:
            raise SystemExit(f"build_site: the page marks metric {metric!r}, which the data does not define")
        return f'<b data-metric="{metric}">{metrics[metric]}</b>'

    out = re.sub(r'<b data-metric="([a-z_]+)">[^<]*</b>', swap, html)
    out = version_assets(out)
    out = region(out, "mast", mast_for(page))
    out = region(out, "footer", FOOTER.format(snapshot=snapshot))
    if TOOLS_OPEN in out:
        start, end = out.index(TOOLS_OPEN), out.index(TOOLS_CLOSE)
        out = out[:start] + TOOLS_OPEN + "\n" + tool_rows() + "\n" + out[end:]
    return out


def build(check: bool) -> int:
    publication = Publication.load(ROOT)
    summary, artifact = publication.read_json("sept11://catalog/summary")
    claims = json.loads(CLAIMS.read_text())
    conflicts = claim_conflicts(summary, claims)
    if conflicts:
        for conflict in conflicts:
            print("CONFLICT:", conflict, file=sys.stderr)
        return 1
    metrics = metrics_from(summary)
    quotes = Quotes(claims)
    snapshot = summary.get("csv_file", "catalog_2026-09-09_pdf.csv")
    stale, records_matched = [], 0
    generated: dict[Path, str] = {}
    for page, _label in PAGES:
        path = DOCS / page
        if not path.is_file():
            print(f"build_site: {page} is in the menu but missing from docs/", file=sys.stderr)
            return 1
        before = path.read_text(encoding="utf-8")
        after = render(before, metrics, page=page, snapshot=snapshot)
        if page == "index.html":
            commitments, _ = publication.read_json("sept11://commitments")
            after = region(after, "purpose", purpose_section(quotes))
            after = region(after, "statements", statements_section(quotes))
            after = region(after, "money", money_ledger(commitments, quotes))
        if page == "examples.html":
            anchors, _ = publication.read_json("sept11://anchors")
            directory, _ = publication.read_json("sept11://help-directory")
            scorecard, _ = publication.read_json("sept11://scorecard")
            commitments, _ = publication.read_json("sept11://commitments")
            summary, _ = publication.read_json("sept11://catalog/summary")
            recent_context, _ = publication.read_json("sept11://release-context")
            after = region(after, "record-demo", record_demo(anchors))
            after = region(after, "help-demo", help_demo(directory, quotes, scorecard))
            after = region(after, "budget-demo", budget_demo(commitments, quotes))
            timeline, _ = publication.read_json("sept11://timeline")
            readings, _ = publication.read_json("sept11://readings")
            recorded, _ = publication.read_json("sept11://examples")
            manifest_html, manifest_csv = manifest_demo(recorded)
            generated[DOCS / "data" / "reading-list.csv"] = manifest_csv
            generated[DOCS / "data" / "obligations.ics"] = calendar.ics(scorecard, SITE_URL)
            after = region(after, "address-demo", address_demo(recorded))
            after = region(after, "timeline-demo", statements_timeline_demo(timeline, quotes))
            after = region(after, "readings-demo", readings_demo(readings, quotes))
            after = region(after, "dates-demo", key_dates_demo(scorecard))
            after = region(after, "cite-demo", cite_demo(recorded))
            after = region(after, "manifest-demo", manifest_html)
            after = region(after, "current-context", current_context_demo(recent_context))
            after = region(after, "research-paths", research_paths_demo(recent_context))
            after = region(after, "release-footprint", release_footprint_demo(summary))
            after = region(after, "release-tracker", release_tracker_demo(scorecard))
            after = region(after, "collection-explorer", collection_explorer_demo(summary))
        if page == "toolkit.html":
            recorded, _ = publication.read_json("sept11://examples")
            after = region(after, "examples", examples_section(recorded))
        if page == "records.html":
            problems, records_matched = records_quote_problems(after, quotes)
            for problem in problems:
                print("PROBLEM:", problem, file=sys.stderr)
            if problems or records_matched == 0:
                print(f"build_site: {page} has {records_matched} cited quotes and {len(problems)} problems",
                      file=sys.stderr)
                return 1
        if after == before:
            continue
        stale.append(page)
        if not check:
            path.write_text(after, encoding="utf-8")
    for path, content in generated.items():
        if path.is_file() and path.read_bytes() == content.encode("utf-8"):
            continue
        stale.append(str(path.relative_to(DOCS)))
        if not check:
            path.write_bytes(content.encode("utf-8"))
    verb = "stale" if check else "written"
    print(f"examined {len(PAGES)} pages, {len(metrics)} metrics, {len(TOOLS)} registered tools and "
          f"{quotes.checked} displayed quotes ({records_matched} on records.html) against {artifact.path} "
          f"({artifact.review_status}); "
          f"{len(stale)} {verb}{': ' + ', '.join(stale) if stale else ''}")
    if check and stale:
        print("build_site: run `python3 scripts/build_site.py` to bring the pages up to date",
              file=sys.stderr)
        return 1
    return 0


def _fixture_quotes() -> Quotes:
    claims = [
        {"id": "q-rule", "claim": "c", "quote": "There is no deadline to enroll.",
         "source": {"type": "url", "url": "https://www.cdc.gov/wtc/about.html", "local_text": "x.txt"}},
        {"id": "q-amount", "claim": "c", "quote": "$34.2 million in FY27",
         "source": {"type": "url", "url": "https://www.nyc.gov/mayors-office/x", "local_text": "x.txt"}},
        {"id": "q-portal", "claim": "c", "quote": "35,000 potential plaintiffs",
         "source": {"type": "portal", "bates": "NYC-WTC_000138296", "page": 1}},
        {"id": "q-figure", "claim": "c", "quote": "145,275",
         "source": {"type": "url", "url": "https://www.cdc.gov/wtc/pdfs/statistics/x.pdf", "local_text": "x.txt"}},
    ]
    return Quotes(claims, locate=False)


def selftest() -> int:
    failures = []
    fixture_anchors = {"anchors": [
        {"id": key, "bates": "NYC-WTC_000000001", "page": 2,
         "collection": "<script>probe</script>", "quote": "<img src=x onerror=probe>"}
        for key in ("harding-memo", "clean-up-initiative", "15-john-street")
    ]}
    demo = record_demo(fixture_anchors)
    if '<script>probe' in demo or '<img src=x' in demo or '.pdf#page=2' not in demo:
        failures.append("record demo must escape source markup and preserve page links")

    quotes = _fixture_quotes()
    directory = {"disclaimer": "d", "programs": [{
        "id": "p", "name": "<b>P</b>", "administered_by": "a", "what_it_is": None,
        "contact": {"phone": "1-800-000-0000", "url": "https://www.cdc.gov/wtc/", "claim_id": "q-rule"},
        "deadline": {"text": "There is no deadline to enroll.", "claim_id": "q-rule"},
        "window": {"text": "There is no deadline to enroll.", "claim_id": "q-rule", "applies_to": "w"},
        "zone": {"name": "z", "definition_url": "https://www.cdc.gov/wtc/e.html", "note": "n"},
        "separate_from": {"text": "There is no deadline to enroll.", "claim_id": "q-rule"},
        "rules": [], "evidence": [{"lane": "L", "examples": [{"text": "There is no deadline to enroll.", "claim_id": "q-rule"}]}],
        "official_pages": []}],
        "nyc_records": [{"id": "i", "who": "w", "issuer": "<i>x</i>", "how": "h",
                         "note": {"text": "There is no deadline to enroll.", "claim_id": "q-rule"},
                         "status_obligation_id": "o"}],
        "mayor_announcement": {"text": "There is no deadline to enroll.", "claim_id": "q-rule"}}
    scorecard = {"rows": [{"id": "o", "status": "partially_observed", "obligation": "x", "source": "s", "due": "2026-12-31"},
                          {"id": "u", "status": "upcoming", "obligation": "later", "source": "s2", "due": "2027-07-14"},
                          {"id": "m", "status": "upcoming", "obligation": "monthly", "source": "s3", "due": "monthly"}]}
    out = help_demo(directory, quotes, scorecard)
    if "<b>P</b>" in out or "<i>x</i>" in out or "partially observed" not in out or 'data-label="Issuer"' not in out:
        failures.append("help demo must escape source text, show the obligation status and label its cells")
    drifted = json.loads(json.dumps(directory))
    drifted["programs"][0]["deadline"]["text"] = "There is no deadline."
    try:
        help_demo(drifted, quotes, scorecard)
        failures.append("a displayed quote that is not the registered quote must stop the build")
    except ValueError:
        pass

    scorecard["generated_at"] = "2026-09-11"
    dates = key_dates_demo(scorecard)
    if (dates.index("December 31, 2026") > dates.index("July 14, 2027") or dates.index("July 14, 2027") > dates.index("monthly")
            or 'data-date="2027-07-14"' not in dates or "obligations.ics" not in dates):
        failures.append("key dates must list dated rows in order, recurring rows apart, and link the calendar file")

    timeline = {"note": "n", "boundary": "b", "topics": ["air"], "sides": {"public_statement": "P", "city_record": "C"},
                "events": [
                    {"id": "a", "date": "2001-10", "date_precision": "month", "side": "city_record", "topic": "air",
                     "title": "<b>t</b>", "source_label": "s", "claim_id": "q-portal", "quote": "35,000 potential plaintiffs",
                     "citation": {"type": "portal", "bates": "NYC-WTC_000138296", "page": 1},
                     "date_basis": {"text": "an email in the same file", "claim_id": "q-portal"}},
                    {"id": "b", "date": "2001-10-03", "date_precision": "day", "side": "public_statement", "topic": "air",
                     "title": "t", "source_label": "s", "claim_id": "q-rule", "quote": "There is no deadline to enroll.",
                     "citation": {"type": "url", "url": "https://www.cdc.gov/wtc/about.html"},
                     "date_basis": {"text": "date of the release"}}]}
    out = statements_timeline_demo(timeline, quotes)
    basis = out[out.index("Date basis:"):]
    if ("<b>t</b>" in out or "October 2001" not in out or "October 3, 2001" not in out
            or "NYC-WTC_000138296.pdf#page=1" not in basis[:basis.index("</p>")]):
        failures.append("the timeline must escape titles, show partial dates as such and link each date basis to its source")
    drifted = json.loads(json.dumps(timeline))
    drifted["events"][1]["quote"] = "There is no deadline."
    try:
        statements_timeline_demo(drifted, quotes)
        failures.append("a timeline quote that is not the registered quote must stop the build")
    except ValueError:
        pass

    readings = {"note": "n", "boundary": "b", "readings": [
        {"id": "r", "analyte": "<i>a</i>", "value_as_printed": "4.5", "unit_as_printed": "%", "medium": "m",
         "location_as_printed": "l", "sample_date": "2001-09-11", "sample_date_precision": "day", "sample_date_basis": "b",
         "document": "d", "document_date": "2001-11-20", "reported_by": "r", "claim_id": "q-portal",
         "quote": "35,000 potential plaintiffs", "citation": {"type": "portal", "bates": "NYC-WTC_000138296", "page": 1}}]}
    out = readings_demo(readings, quotes)
    if "<i>a</i>" in out or "<b>4.5 %</b>" not in out or "September 11, 2001" not in out:
        failures.append("readings must escape text and show the value and unit as printed")

    folder = {"source": "s", "box": "b", "folder": "<script>", "documents": 3, "pages": 3, "first_bates": "NYC-WTC_000000001",
              "first_pdf_url": "https://sept11documents.cityofnewyork.us/apps/content/September11_MD/NYC-WTC_000000001.pdf",
              "printed_identifiers": {"bins": ["1001217"], "block": "79", "lot": None}}
    calls = {"examples": [
        {"id": "addr", "demo": "address", "is_error": False, "request": {"name": "building_lookup", "arguments": {"address": "15 John"}},
         "response": {"data": {"query": {"read_as": "15 JOHN"}, "folders": [folder], "folders_matched": 2, "folders_returned": 1,
                               "documents_in_matched_folders": 3, "catalog_captured_at": "2026-09-11T00:00:00"}}},
        {"id": "cite", "demo": "cite", "is_error": False, "title": "t", "question": "q",
         "request": {"name": "citations_format", "arguments": {"bates": "NYC-WTC_000000001", "page": 1}},
         "response": {"data": {"bates": "NYC-WTC_000000001", "page": 1, "pdf_url": "https://x/#page=1", "stamp_status": "unverified_stamp", "page_text_read": False,
                               "catalog": None, "formats": {"short": "s", "full": 'f "quoted"', "markdown": "[m](u)"}}}},
        {"id": "list", "demo": "manifest", "is_error": False,
         "request": {"name": "records_manifest", "arguments": {"bates": ["NYC-WTC_000000001"]}},
         "response": {"data": {"requested": 2, "found": 1, "missing": ["NYC-WTC_000000009"], "catalog_captured_at": "2026-09-11",
                               "csv": "bates\r\n", "records": [{"bates": "NYC-WTC_000000001", "source": "s", "box": "", "folder": "f",
                                                                 "page_count": 2, "pdf_size": 1_500_000, "pdf_url": "https://x"}]}}}]}
    out = address_demo(calls)
    if "<script>" in out or "BIN 1001217, block 79" not in out or "Showing 1 of 2" not in out or "#example-addr" not in out:
        failures.append("the address demo must escape labels, show printed identifiers, say when it truncates and link the call")
    out = cite_demo(calls)
    if 'data-copy="f &quot;quoted&quot;"' not in out or "printed stamp was not checked" not in out:
        failures.append("the citation demo must escape copy text and say when the stamp was not read")
    out, csv_text = manifest_demo(calls)
    if "1.5 MB" not in out or "NYC-WTC_000000009" not in out or csv_text != "bates\r\n":
        failures.append("the reading list must show sizes, name missing numbers and pass the CSV through")
    try:
        recorded_for({"examples": []}, "address")
        failures.append("a demo with no recorded call must stop the build")
    except ValueError:
        pass

    from mcp_checks import fixture_ledger  # the same fixture the server gates use
    fixture = fixture_ledger()
    fixture["stage_labels"] = {s: s for s in fixture["stages"]}
    for row in fixture["commitments"]:
        row.update({"amount_display": "$34.2 million", "description": "d", "questions": ["q"], "claim_id": "q-amount"})
        row["stages"]["announcement"].update({"claim_id": "q-amount", "url": "https://www.nyc.gov/mayors-office/x",
                                              "label": "<script>"})
    out = budget_demo(fixture, quotes)
    if out.count('<article class="budget-result"') != 3 or "<script>" in out or 'data-stage="payment"' not in out:
        failures.append("budget demo must render every row with five stages and escape labels")
    tiles = money_ledger(fixture, quotes)
    if tiles.count("<div><b>") != 3 or "<script>" in tiles or "total" in tiles.lower():
        failures.append("the money ledger must show one tile per row, escaped, with no total")
    summed = json.loads(json.dumps(fixture))
    summed["total"] = "3000000"
    try:
        budget_demo(summed, quotes)
        failures.append("a ledger with a total must not reach the page")
    except Exception:
        pass
    wrong = json.loads(json.dumps(fixture))
    wrong["commitments"][0]["amount_display"] = "$99 million"
    try:
        budget_demo(wrong, quotes)
        failures.append("an amount that is not in its claim's quote must not reach the page")
    except ValueError:
        pass

    recorded = {"recorded_at": "2026-09-11T00:00:00+00:00", "server_version": "0.2.0", "schema_version": "s",
                "examples": [{"id": "x", "demo": "records", "title": "t</pre><script>", "question": "q</pre><script>",
                              "request": {"name": "catalog_search", "arguments": {"text": "</pre>"}},
                              "is_error": False, "response": {"warnings": [{"code": "local_snapshot_only"}], "data": "</pre>"},
                              "redactions": []}]}
    out = examples_section(recorded)
    if "</pre><script>" in out or out.count("<pre") != 2:
        failures.append("examples must escape recorded text inside <pre>")

    context = {"as_of": "2026-09-21", "window": "w", "scope_note": "n", "items": [
        {"date": "d", "kind": "<b>k</b>", "title": "<script>x</script>", "summary": "s",
         "source_label": "l", "source_url": "https://example.com/", "use": "u"}],
        "paths": [{"id": "p", "title": "t<script>", "question": "q<script>", "tools": "a → b", "answer": "a", "boundary": "b"}]}
    if "<script>" in current_context_demo(context) or "<script>" in research_paths_demo(context):
        failures.append("current context and research paths must escape data text")
    generated_collection = collection_explorer_demo({"by_source": {"New <source>": 1}, "by_agency": {}})
    if "New <source>" in generated_collection or "Inspect the returned catalog rows" not in generated_collection:
        failures.append("collection explorer must escape and render a newly appearing source")

    purpose = purpose_section(quotes, [
        [("<b>lead</b>", "q-rule", "quote", "as <published>")],
        [("As of", "q-figure", "figure", "members <enrolled>"), ("and", "q-portal", "quote", None)],
    ])
    if ("<b>lead</b>" in purpose or "<published>" in purpose or "<enrolled>" in purpose
            or purpose.count("<q>") != 2 or "145,275 members" not in purpose
            or "NYC-WTC_000138296 · p.1" not in purpose or "NYC-WTC_000138296.pdf#page=1" not in purpose):
        failures.append("the purpose section must escape leads and notes, show figures as text and cite portal pages")
    try:
        purpose_section(quotes, [[("", "q-rule", "quote", None), ("", "no-such-claim", "quote", None)]])
        failures.append("a purpose line naming an unregistered claim must stop the build")
    except ValueError:
        pass

    records = ('<div><blockquote>"There is no deadline to enroll."</blockquote>'
               '<a class="cite" href="https://www.cdc.gov/wtc/about.html">CDC</a></div>'
               '<div><blockquote>"approximately 35,000 potential plaintiffs"</blockquote><a class="cite" '
               'href="https://sept11documents.cityofnewyork.us/apps/content/September11_MD/NYC-WTC_000138296.pdf#page=1">'
               'NYC-WTC_000138296 · p.1</a></div>')
    problems, matched = records_quote_problems(records, quotes)
    if problems or matched != 2:
        failures.append(f"a records page whose quotes are registered and correctly cited must pass: {problems}")
    for name, broken in [("drifted quote", records.replace("deadline to enroll", "deadline to apply")),
                         ("wrong source", records.replace("wtc/about.html", "wtc/other.html")),
                         ("wrong page", records.replace("#page=1", "#page=2"))]:
        if not records_quote_problems(broken, quotes)[0]:
            failures.append(f"records gate must catch a {name}")

    fixture_summary = {"documents": 10, "pages": 20, "pdf_bytes": 2_500_000_000,
                       "by_source": {"a": 6, "b": 4}, "folders": 7}
    metrics = metrics_from(fixture_summary)
    if metrics != {"documents": "10", "pages": "20", "pdf_bytes": "2.5 GB",
                   "collections": "2", "folders": "7"}:
        failures.append(f"metric formatting: {metrics}")

    # A distinctive sentinel: "old" would match inside the word "folder" in a generated blurb.
    page = ('<b data-metric="documents">0</b><b data-metric="pages">0</b>'
            '<!-- BUILD:mast --><!-- /BUILD:mast -->'
            '<!-- BUILD:footer --><!-- /BUILD:footer -->'
            f'{TOOLS_OPEN}\n<tr>STALE-ROW-SENTINEL</tr>\n{TOOLS_CLOSE}')
    out = render(page, metrics, page="examples.html")
    if 'aria-current="page"' not in out or out.count("<nav class=\"top\"") != 1:
        failures.append("the menu was not written with the current page marked")
    if "not affiliated with the City" not in out:
        failures.append("the footer region was not written")
    versioned = render('<link rel="stylesheet" href="assets/site.css">'
                       '<script src="assets/site.js?v=deadbeef"></script>', metrics)
    if f"site.css?v={asset_version('site.css')}" not in versioned or "deadbeef" in versioned:
        failures.append("asset URLs were not stamped with the current file hash")
    if '<b data-metric="documents">10</b>' not in out or "STALE-ROW-SENTINEL" in out:
        failures.append("render did not replace the marked regions")
    if "portal_get_page_text" not in out or "budget_lookup" not in out:
        failures.append("tool rows are not generated from the registry")

    conflicts = claim_conflicts({"documents": 99, "pages": 20}, [{"id": "x", "expected": {"documents": 10}}])
    if not conflicts:
        failures.append("a contradicted number was not caught")
    try:
        render('<b data-metric="unknown_metric">1</b>', metrics)
        failures.append("an unknown metric marker was accepted")
    except SystemExit:
        pass

    total = 30
    for message in failures:
        print("FAIL:", message)
    print(f"build_site selftest: {len(failures)} failures, {total - len(failures)}/{total} checks passed over "
          f"{len(metrics)} metrics, {len(TOOLS)} registered tools, 16 generated demos, the purpose section and "
          f"the records gate")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fail if the page and the data disagree")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    return build(args.check)


if __name__ == "__main__":
    sys.exit(main())
