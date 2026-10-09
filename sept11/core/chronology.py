"""The statements-and-records timeline and the located sampling results (spec 02 §3.8, §3.9).

Both files are curated: every row names a registered claim and carries that claim's
quote, and `scripts/build_site.py` refuses a row whose quote differs from the claim or
cannot be located in the captured source. This module checks the shape of the files
and selects rows; it computes no date, converts no unit and ranks nothing.
"""
from __future__ import annotations

import re

SIDES = ("public_statement", "city_record", "later_review")
PRECISION = ("day", "month", "year")
_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
_PRECISION_LENGTH = {"year": 4, "month": 7, "day": 10}


def check_date(value, label: str) -> str:
    if not isinstance(value, str) or not _DATE.match(value):
        raise ValueError(f"{label} must be YYYY, YYYY-MM or YYYY-MM-DD, got {value!r}")
    return value


def span(value: str) -> tuple[str, str]:
    """The first and last day a partial date can mean, as comparable strings.

    `2001-10` covers 2001-10-01 through 2001-10-31; the end uses day 99 so a string
    comparison keeps every day of the month inside it without a calendar.
    """
    parts = value.split("-")
    start = "-".join(parts + ["01"] * (3 - len(parts)))
    end = "-".join(parts + ["99"] * (3 - len(parts)))
    return start, end


def overlaps(value: str, date_from: str | None, date_to: str | None) -> bool:
    start, end = span(value)
    if date_from and end < span(date_from)[0]:
        return False
    if date_to and start > span(date_to)[1]:
        return False
    return True


def _citation_problems(row_id: str, citation) -> list[str]:
    if not isinstance(citation, dict):
        return [f"{row_id}: citation must be an object"]
    if citation.get("type") == "portal":
        ok = (isinstance(citation.get("bates"), str) and re.fullmatch(r"NYC-WTC_[0-9]+", citation["bates"])
              and type(citation.get("page")) is int and citation["page"] >= 1)
        return [] if ok else [f"{row_id}: a portal citation needs a Bates number and a one-based page"]
    if citation.get("type") == "url":
        return [] if str(citation.get("url", "")).startswith("https://") else [f"{row_id}: a URL citation must be HTTPS"]
    return [f"{row_id}: citation type must be portal or url"]


def timeline_problems(document: dict) -> list[str]:
    problems = []
    topics = document.get("topics") or []
    seen = set()
    previous = ""
    for row in document.get("events", []):
        rid = row.get("id") or "(no id)"
        if rid in seen:
            problems.append(f"{rid}: duplicate id")
        seen.add(rid)
        try:
            check_date(row.get("date"), f"{rid}.date")
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if row.get("date_precision") not in PRECISION or len(row["date"]) != _PRECISION_LENGTH.get(row.get("date_precision"), 0):
            problems.append(f"{rid}: date_precision must match the date's length")
        # A month sorts at its first day, so "2001-10" comes before "2001-10-03".
        if span(row["date"])[0] < previous:
            problems.append(f"{rid}: events must be in date order")
        previous = span(row["date"])[0]
        if row.get("side") not in SIDES:
            problems.append(f"{rid}: side must be one of {SIDES}")
        if row.get("topic") not in topics:
            problems.append(f"{rid}: topic {row.get('topic')!r} is not declared in `topics`")
        for key in ("title", "source_label", "claim_id", "quote"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                problems.append(f"{rid}: {key} is required")
        basis = row.get("date_basis") or {}
        if not isinstance(basis.get("text"), str) or not basis["text"].strip():
            problems.append(f"{rid}: every date needs a stated basis")
        if basis.get("citation") is not None:
            problems += _citation_problems(rid + ".date_basis", basis["citation"])
        problems += _citation_problems(rid, row.get("citation"))
    if not seen:
        problems.append("the timeline has no events")
    return problems


def readings_problems(document: dict) -> list[str]:
    problems = []
    seen = set()
    for row in document.get("readings", []):
        rid = row.get("id") or "(no id)"
        if rid in seen:
            problems.append(f"{rid}: duplicate id")
        seen.add(rid)
        for key in ("analyte", "value_as_printed", "medium", "location_as_printed", "document",
                    "claim_id", "quote", "sample_date_basis"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                problems.append(f"{rid}: {key} is required")
        if not isinstance(row.get("unit_as_printed"), str):
            problems.append(f"{rid}: unit_as_printed must be a string (empty when the document prints none)")
        for key in ("sample_date", "document_date"):
            try:
                check_date(row.get(key), f"{rid}.{key}")
            except ValueError as exc:
                problems.append(str(exc))
        if isinstance(row.get("value_as_printed"), (int, float)):
            problems.append(f"{rid}: values stay as printed text, never numbers")
        problems += _citation_problems(rid, row.get("citation"))
    if not seen:
        problems.append("the readings file has no rows")
    return problems


def validate(document: dict, kind: str) -> None:
    found = timeline_problems(document) if kind == "timeline" else readings_problems(document)
    if found:
        raise ValueError(f"{kind} file failed validation: " + "; ".join(found[:5]))


def select_events(document: dict, side: str = "all", topic: str = "all",
                  date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    return [row for row in document["events"]
            if (side == "all" or row["side"] == side) and (topic == "all" or row["topic"] == topic)
            and overlaps(row["date"], date_from, date_to)]


def select_readings(document: dict, analyte: str = "", location: str = "") -> list[dict]:
    analyte, location = analyte.lower(), location.lower()
    return [row for row in document["readings"]
            if analyte in row["analyte"].lower() and location in row["location_as_printed"].lower()]


def selftest() -> list[str]:
    failures = []
    if not overlaps("2001-10", "2001-10-20", "2001-10-20") or overlaps("2001-10", "2001-11-01", None):
        failures.append("a month must overlap a day inside it and not the next month")
    if overlaps("2002", None, "2001-12-31") or not overlaps("2002", "2002-06", "2002-06"):
        failures.append("a year must overlap its months only")
    row = {"id": "a", "date": "2001-10", "date_precision": "day", "side": "public_statement", "topic": "air",
           "title": "t", "source_label": "s", "claim_id": "c", "quote": "q", "date_basis": {"text": "b"},
           "citation": {"type": "portal", "bates": "NYC-WTC_000000001", "page": 1}}
    if not any("date_precision" in p for p in timeline_problems({"topics": ["air"], "events": [row]})):
        failures.append("a month date with day precision must be refused")
    reading = {"id": "r", "analyte": "a", "value_as_printed": 4.5, "unit_as_printed": "%", "medium": "m",
               "location_as_printed": "l", "document": "d", "claim_id": "c", "quote": "q",
               "sample_date_basis": "b", "sample_date": "2001-09-11", "document_date": "2001-11-20",
               "citation": {"type": "url", "url": "http://x"}}
    found = readings_problems({"readings": [reading]})
    if not any("printed text" in p for p in found) or not any("HTTPS" in p for p in found):
        failures.append("a float value and a non-HTTPS citation must be refused")
    return failures
