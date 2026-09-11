#!/usr/bin/env python3
"""Bounded client for the NYC September 11th Document Portal.

The portal (sept11documents.cityofnewyork.us, a.k.a. nyc.gov/sept11docs) is a
Mindbreeze InSpire search appliance. Its HTML is an empty shell; everything
useful is behind an anonymous JSON API that was verified live on 2026-09-09:

    POST {base}/api/v2/search   query + properties + facets            (JSON)
    POST {base}/api/v2/export   CSV/XLSX/JSON of *every* result of a query
    GET  {front}/apps/content/September11_MD/{BATES}.pdf   the document

Documents are identified by Bates numbers (NYC-WTC_000136670). There is no
document-date metadata; `mes:date` is the index time.

Etiquette: one honest User-Agent, at
least one second between requests to the same host, retry only on 429/5xx,
validate the PDF magic before trusting a download, cache what you fetch.

This module is the only place in the toolkit that opens a socket to the City.
It refuses anything that is not an HTTPS request on port 443 to an allowlisted
host, refuses redirects, bounds every response, and (when handed a Budget)
spaces requests and charges bytes across processes.

The CLI lives in `scripts/portal_api.py`; `sept11.mcp` never calls the fetching
methods here — serving surfaces read the cache instead (spec 10).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from ..core.citations import CONTENT_PATH, normalize_bates
from ..core.errors import Sept11Error

FRONT = "https://sept11documents.cityofnewyork.us"
BACKEND = "https://nyc.mindbreeze.com/search/september-11"
USER_AGENT = (
    "sept11-toolkit/0.1 (+https://github.com/pranava0x0/sept11documents-mcp; "
    "open civic research tool; contact via repository issues)"
)
ALLOWED_HOSTS = {"sept11documents.cityofnewyork.us", "nyc.mindbreeze.com"}
RETRY_STATUSES = {429, 500, 502, 503, 504}

# Properties the portal exposes per document (all verified 2026-09-09).
CATALOG_PROPERTIES = [
    "mes:key", "title", "source", "agency", "box_name", "folder_name",
    "page_count", "pdf_size", "production_volume", "production_end",
    "related_document", "mes:date",
]
SEARCH_PROPERTIES = CATALOG_PROPERTIES + ["content"]


class PortalError(Sept11Error):
    """A portal request failed in a way a retry will not fix."""

    code = "portal_error"


def _props(names: list[str]) -> list[dict]:
    return [
        {"name": n, "formats": ["HTML"] if n == "content" else ["VALUE"]}
        for n in names
    ]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Portal endpoints are fixed; redirects require an explicit adapter review."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PortalError("portal redirect refused; review canonical endpoint")


def read_bounded(response, limit: int) -> bytes:
    chunks, total = [], 0
    while True:
        chunk = response.read(min(65536, limit + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise PortalError("response exceeds configured byte limit")


class PortalClient:
    """Thin, rate-limited client. One instance per process is plenty."""

    def __init__(self, base: str = FRONT, min_interval: float = 1.0,
                 timeout: float = 60.0, user_agent: str = USER_AGENT,
                 log=None, max_response_bytes: int = 64 * 1024 * 1024,
                 budget=None):
        self.base = base.rstrip("/")
        self.min_interval = min_interval
        self.timeout = timeout
        self.user_agent = user_agent
        self.log = log or (lambda message: print(message, file=sys.stderr))
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        self.max_response_bytes = max_response_bytes
        # A sept11.storage.budget.Budget spaces requests and charges bytes across
        # processes. Without one, politeness is per-process only.
        self.budget = budget
        self._opener = urllib.request.build_opener(NoRedirect())
        self._last: dict[str, float] = {}
        self.requests_made = 0

    # -- transport -----------------------------------------------------------
    def _throttle(self, url: str) -> None:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        try:
            valid = (parsed.scheme == "https" and parsed.port in (None, 443)
                     and parsed.username is None and parsed.password is None
                     and not parsed.fragment and host in ALLOWED_HOSTS)
        except ValueError:
            valid = False
        if not valid:
            raise PortalError(f"refusing to contact non-allowlisted host {host!r}")
        if self.budget is not None:
            self.budget.wait_turn(host, self.min_interval)
            self._last[host] = time.monotonic()
            return
        wait = self._last.get(host, 0.0) + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def _request(self, url: str, body: bytes | None, accept: str,
                 timeout: float | None = None, method: str | None = None) -> tuple[bytes, str]:
        headers = {"User-Agent": self.user_agent, "Accept": accept}
        if body is not None:
            headers["Content-Type"] = "application/json"
        attempts, delay = 3, 2.0
        for attempt in range(1, attempts + 1):
            self._throttle(url)
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                    self.requests_made += 1
                    body = read_bounded(resp, self.max_response_bytes)
                    if self.budget is not None:
                        # Retries spend from the same budget: charge what arrived, always.
                        self.budget.spend(len(body))
                    return body, resp.headers.get("Content-Type", "")
            except urllib.error.HTTPError as e:
                self.requests_made += 1
                if e.code in RETRY_STATUSES and attempt < attempts:
                    self.log(f"portal_api: HTTP {e.code} on {url}; retry {attempt}/{attempts - 1} in {delay:.0f}s")
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise PortalError(f"HTTP {e.code} for {url}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                self.requests_made += 1
                raise PortalError("portal network failure") from e
        raise PortalError("unreachable")  # pragma: no cover

    def _post_json(self, path: str, payload: dict, timeout: float | None = None) -> dict:
        raw, ctype = self._request(self.base + path, json.dumps(payload).encode("utf-8"),
                                   "application/json", timeout=timeout)
        stripped = raw.lstrip()
        if not stripped.startswith(b"{"):
            raise PortalError(
                f"expected JSON from {path}, got {ctype or 'unknown type'} (body withheld)")
        return json.loads(raw.decode("utf-8"))

    # -- search --------------------------------------------------------------
    def search(self, query: str, count: int = 10, properties: list[str] | None = None,
               facets: list[str] | None = None, max_facet_entries: int = 100,
               content_sample_length: int = 300, orderby: str | None = None,
               paging_state: dict | None = None) -> dict:
        body: dict = {
            "user": {"query": {"unparsed": query}},
            "count": count,
            "content_sample_length": content_sample_length,
            "properties": _props(properties or SEARCH_PROPERTIES),
        }
        if facets:
            body["facets"] = [{"name": f, "max_entries": max_facet_entries} for f in facets]
        if orderby:
            body["orderby"] = orderby
        if paging_state:
            body["paging_states"] = paging_state if isinstance(paging_state, list) else [paging_state]
            body["paging"] = {"direction": "NEXT"}
        return self._post_json("/api/v2/search", body)

    def iter_results(self, query: str, page_size: int = 100, max_results: int | None = None,
                     properties: list[str] | None = None):
        """Yield simplified results page by page using Mindbreeze paging states."""
        seen = 0
        paging_state = None
        seen_states, seen_ids = set(), set()
        while True:
            resp = self.search(query, count=page_size, properties=properties,
                               paging_state=paging_state)
            rs = resp.get("resultset", {})
            results = rs.get("results", [])
            if not results:
                return
            for r in results:
                row = simplify_result(r)
                if row["bates"] in seen_ids:
                    raise PortalError("duplicate result during paging; coverage uncertain")
                seen_ids.add(row["bates"])
                yield row
                seen += 1
                if max_results and seen >= max_results:
                    return
            if not rs.get("next_avail"):
                return
            paging_state = rs.get("paging_state") or [
                service["paging_state"] for service in rs.get("per_service_dataset", [])
                if service.get("paging_state")]
            if not paging_state:
                raise PortalError("more results exist but paging state is missing")
            state_key = json.dumps(paging_state, sort_keys=True)
            if state_key in seen_states:
                raise PortalError("repeated paging state; coverage uncertain")
            seen_states.add(state_key)

    def facets(self, names: list[str], query: str = "ALL extension:pdf",
               max_entries: int = 200) -> dict[str, list[tuple[str, int]]]:
        resp = self.search(query, count=1, properties=["mes:key"], facets=names,
                           max_facet_entries=max_entries)
        out: dict[str, list[tuple[str, int]]] = {}
        for f in resp.get("facets", []):
            entries = []
            for e in f.get("entries", []):
                val = e.get("value", {})
                label = val.get("str") if isinstance(val, dict) else None
                entries.append((label or e.get("html", ""), int(e.get("count", 0))))
            out[f.get("name") or f.get("id", "?")] = entries
        return out

    # -- catalog export ------------------------------------------------------
    def export_csv(self, query: str = "ALL extension:pdf",
                   properties: list[str] | None = None, batch_size: int = 1000) -> str:
        payload = {
            "search_request": {
                "count": 100,
                "properties": _props(properties or CATALOG_PROPERTIES),
                "user": {"query": {"and": [{"unparsed": query, "id": "query"}]}, "constraints": []},
                "source_context": {"constraints": [{"unparsed": "ALL", "id": "view_base"}]},
            },
            "export_format": "text/csv",
            "batch_size": batch_size,
            "allow_duplicate": False,
            "groupby_properties": [],
        }
        raw, ctype = self._request(self.base + "/api/v2/export",
                                   json.dumps(payload).encode("utf-8"), "text/csv", timeout=600)
        text = raw.decode("utf-8-sig")
        if "text/csv" not in ctype and not text.startswith("Mindbreeze Key"):
            raise PortalError(f"export returned {ctype!r}, not CSV")
        return text

    # -- documents -----------------------------------------------------------
    def pdf_url(self, bates: str) -> str:
        return self.base + CONTENT_PATH.format(bates=normalize_bates(bates))

    def fetch_pdf(self, bates: str, dest_dir: Path, expected_size: int | None = None) -> Path:
        bates = normalize_bates(bates)
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / f"{bates}.pdf"
        if out.is_symlink():
            raise PortalError("refusing symlink PDF cache entry")
        if out.exists():
            with out.open("rb") as cached:
                valid_magic = cached.read(5) == b"%PDF-"
            if expected_size is not None and out.stat().st_size != expected_size:
                raise PortalError("cached PDF size differs from catalog; review before replacement")
            if valid_magic and out.stat().st_size > 5:
                return out
        raw, ctype = self._request(self.pdf_url(bates), None, "application/pdf", timeout=600)
        if raw[:5] != b"%PDF-":
            raise PortalError(f"{bates}: body is not a PDF ({ctype!r}; body withheld)")
        if expected_size is not None and len(raw) != expected_size:
            raise PortalError(f"{bates}: PDF size differs from catalog; not cached")
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(dir=dest_dir, prefix=".pdf-", delete=False) as tmp:
                tmp_name = tmp.name
                tmp.write(raw)
            os.replace(tmp_name, out)
        finally:
            if tmp_name and os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return out


# -- pure helpers (covered by --selftest) -------------------------------------

def _value(prop: dict):
    data = prop.get("data") or []
    if not data or data[0] is None:
        return None
    item = data[0]
    val = item.get("value")
    if isinstance(val, dict):
        if "str" in val:
            return val["str"]
        if "num" in val:
            return val["num"]
    if val is None and "html" in item:
        return item["html"]
    return val


def simplify_result(result: dict) -> dict:
    props = {p.get("id"): _value(p) for p in result.get("properties", [])}
    key = props.get("mes:key") or props.get("title") or ""
    try:
        bates = normalize_bates(str(key))
    except ValueError:
        bates = str(key)
    page_count = props.get("page_count")
    pdf_size = props.get("pdf_size")
    return {
        "bates": bates,
        "title": props.get("title"),
        "source": props.get("source"),
        "agency": props.get("agency"),
        "box": props.get("box_name"),
        "folder": props.get("folder_name"),
        "page_count": int(float(page_count)) if page_count not in (None, "") else None,
        "pdf_size": int(float(pdf_size)) if pdf_size not in (None, "") else None,
        "production_volume": props.get("production_volume"),
        "production_end": props.get("production_end"),
        "indexed_at_ms": props.get("mes:date"),
        "snippet_html": props.get("content"),
        "pdf_url": FRONT + CONTENT_PATH.format(bates=bates) if bates.startswith("NYC-WTC_") else None,
    }


def parse_catalog_csv(text: str) -> list[dict]:
    """Parse a Mindbreeze CSV export (';' delimited, BOM, trailing query column)."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=";"))
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    idx = {h: i for i, h in enumerate(header)}

    def col(row, name):
        i = idx.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    required = {"Name", "Mindbreeze Key", "page_count", "pdf_size"}
    if not required.issubset(idx) or len(header) != len(set(header)):
        raise PortalError("catalog schema missing or duplicate columns")
    out, seen = [], set()
    for row in rows[1:]:
        if not row or not any(row):
            continue
        name = col(row, "Name") or col(row, "Mindbreeze Key")
        try:
            bates = normalize_bates(name)
        except ValueError as exc:
            raise PortalError("catalog contains invalid document ID") from exc
        if bates in seen:
            raise PortalError("catalog contains duplicate document ID")
        seen.add(bates)
        pc, sz = col(row, "page_count"), col(row, "pdf_size")
        if not pc.isdecimal() or not sz.isdecimal() or int(pc) < 1 or int(sz) < 1:
            raise PortalError("catalog contains invalid page count or PDF size")
        out.append({
            "bates": bates,
            "key": col(row, "Mindbreeze Key"),
            "source": col(row, "source"),
            "agency": col(row, "agency"),
            "box": col(row, "box_name"),
            "folder": col(row, "folder_name"),
            "page_count": int(float(pc)) if pc else None,
            "pdf_size": int(float(sz)) if sz else None,
            "production_volume": col(row, "production_volume"),
            "production_end": col(row, "production_end"),
            "indexed_at": col(row, "Date"),
        })
    return out


def summarize_catalog(rows: list[dict]) -> dict:
    from collections import Counter
    pages = sum(r["page_count"] or 0 for r in rows)
    size = sum(r["pdf_size"] or 0 for r in rows)
    return {
        "documents": len(rows),
        "pages": pages,
        "pdf_bytes": size,
        "by_source": dict(Counter(r["source"] for r in rows).most_common()),
        "by_agency": dict(Counter(r["agency"] for r in rows).most_common()),
        "by_production_volume": dict(sorted(Counter(r["production_volume"] for r in rows).items())),
        "collections": len({r["source"] for r in rows}),
        "boxes": len({r["box"] for r in rows}),
        "folders": len({r["folder"] for r in rows}),
        "largest_document": max(rows, key=lambda r: r["page_count"] or 0)["bates"] if rows else None,
    }


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# -- selftest ------------------------------------------------------------------

_FIXTURE_CSV = (
    "﻿Mindbreeze Key;Name;source;agency;box_name;folder_name;page_count;pdf_size;"
    "production_volume;production_end;related_document;Date;Search for “ALL extension:pdf”\n"
    "NYC-WTC0003/SPDF/PDF001/NYC-WTC_000058159.pdf;NYC-WTC_000058159.pdf;DEP Hard Copies (68 Boxes);"
    "Environmental Protection, Dept. of;DEP Box 57;GCMS X 3/2/02 A 3/27/02;1;14471;NYC-WTC0003;"
    "NYC-WTC_000058159;\"header {\n  key: \"\"x_MD\"\"\n}\";2026-08-06;\n"
    "NYC-WTC_000136670;NYC-WTC_000136670.pdf;DORIS Giuliani;Records and Information Services, Dept. of;"
    ";Folder 140;149;4584946;NYC-WTC0005;NYC-WTC_000136818;;2026-08-06;\n"
)

_FIXTURE_RESULT = {
    "id": "september11 Connector:September11_MD:NYC-WTC_000136670:",
    "properties": [
        {"id": "mes:key", "data": [{"value": {"str": "NYC-WTC_000136670"}}]},
        {"id": "title", "data": [{"value": {"str": "NYC-WTC_000136670.pdf"}}]},
        {"id": "page_count", "data": [{"value": {"str": "149"}}]},
        {"id": "pdf_size", "data": [{"value": {"num": 4584946.0}}]},
        {"id": "source", "data": [{"value": {"str": "DORIS Giuliani"}}]},
        {"id": "content", "data": [{"html": "Re: <em>Legislative Alternatives</em>"}]},
        {"id": "related_document", "data": [None]},
    ],
}


def selftest() -> int:
    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    check(normalize_bates("NYC-WTC0003/SPDF/PDF001/NYC-WTC_000058159.pdf") == "NYC-WTC_000058159", "normalize path")
    check(normalize_bates("NYC-WTC_000138296.pdf_MD") == "NYC-WTC_000138296", "normalize _MD")
    try:
        normalize_bates("not-a-bates")
        check(False, "normalize should reject garbage")
    except ValueError:
        pass
    rows = parse_catalog_csv(_FIXTURE_CSV)
    check(len(rows) == 2, f"fixture rows == 2, got {len(rows)}")
    check(rows[0]["bates"] == "NYC-WTC_000058159" and rows[0]["page_count"] == 1, "row 0 parsed")
    check(rows[1]["folder"] == "Folder 140" and rows[1]["pdf_size"] == 4584946, "row 1 parsed")
    s = summarize_catalog(rows)
    check(s["documents"] == 2 and s["pages"] == 150 and s["largest_document"] == "NYC-WTC_000136670", "summary")
    r = simplify_result(_FIXTURE_RESULT)
    check(r["bates"] == "NYC-WTC_000136670" and r["page_count"] == 149 and r["pdf_size"] == 4584946, "simplify")
    check(r["pdf_url"] == FRONT + "/apps/content/September11_MD/NYC-WTC_000136670.pdf", "pdf url")
    check("<em>" in (r["snippet_html"] or ""), "snippet html kept")
    c = PortalClient()
    try:
        c._throttle("https://example.com/x")
        check(False, "allowlist must reject example.com")
    except PortalError:
        pass
    for f in failures:
        print("FAIL:", f)
    print(f"portal_api selftest: {len(failures)} failures, {8 - len(failures)}/8 groups passed")
    return 1 if failures else 0
