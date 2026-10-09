"""Dated obligations as a list and as an iCalendar file.

The scorecard's `due` field is the date basis stated in the settlement or the
resolution. Only full ISO dates become calendar entries; a rolling basis such as
"monthly through 2027-08" stays a row with no computed dates, because inventing
the meeting days would put dates on a calendar that no source gives.
"""
from __future__ import annotations

import datetime as dt
import re

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UID_DOMAIN = "sept11documents-toolkit"


def parse_day(value, label: str = "as_of") -> dt.date:
    if not isinstance(value, str) or not _ISO_DAY.match(value):
        raise ValueError(f"{label} must be an ISO date such as 2026-10-08")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} is not a calendar date: {value}") from exc


def schedule(scorecard: dict, as_of: dt.date) -> dict:
    """Rows with a full date, ordered, with days from `as_of`; rolling rows listed apart."""
    dated, rolling = [], []
    for row in scorecard.get("rows", []):
        due = row.get("due")
        if not due:
            continue
        base = {"id": row["id"], "obligation": row["obligation"], "source": row.get("source"),
                "status": row["status"], "checked": row.get("checked"), "due_as_stated": due}
        if _ISO_DAY.match(due):
            day = dt.date.fromisoformat(due)
            dated.append({**base, "date": due, "days_from_as_of": (day - as_of).days})
        else:
            rolling.append(base)
    dated.sort(key=lambda r: (r["date"], r["id"]))
    return {"dated": dated, "rolling": rolling}


def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> list[str]:
    """RFC 5545 §3.1: lines over 75 octets continue on the next line after one space."""
    out, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > (75 if not out else 74):
            out.append(current)
            current = char
        else:
            current += char
    out.append(current)
    return [out[0]] + [" " + part for part in out[1:]]


def ics(scorecard: dict, site_url: str) -> str:
    """All-day events for each dated obligation. DTSTAMP is the scorecard's date so the
    file is byte-stable between builds of the same data."""
    stamp = str(scorecard.get("generated_at", "1970-01-01"))[:10].replace("-", "") + "T000000Z"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{UID_DOMAIN}//obligations//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:September 11th records obligations"]
    for row in schedule(scorecard, dt.date(1970, 1, 1))["dated"]:
        day = dt.date.fromisoformat(row["date"])
        description = (f"Source: {row['source']}. Status when checked ({row['checked']}): "
                       f"{row['status'].replace('_', ' ')}. An observation of public surfaces, "
                       f"with no legal determination. {site_url}")
        lines += ["BEGIN:VEVENT", f"UID:{row['id']}@{UID_DOMAIN}", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
                  f"DTEND;VALUE=DATE:{(day + dt.timedelta(days=1)).strftime('%Y%m%d')}",
                  f"SUMMARY:{_escape(row['obligation'])}", f"DESCRIPTION:{_escape(description)}",
                  "TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    folded = [part for line in lines for part in _fold(line)]
    return "\r\n".join(folded) + "\r\n"


def selftest() -> list[str]:
    failures = []
    card = {"generated_at": "2026-09-11", "rows": [
        {"id": "b", "obligation": "Report, with commas; and more", "status": "upcoming", "due": "2027-07-14",
         "source": "Res. 560-A §2", "checked": "2026-09-11"},
        {"id": "a", "obligation": "Update", "status": "observed", "due": "2025-12-31", "source": "s", "checked": "c"},
        {"id": "m", "obligation": "Monthly", "status": "upcoming", "due": "monthly through 2027-08", "source": "s"},
        {"id": "n", "obligation": "No date", "status": "observed"}]}
    plan = schedule(card, dt.date(2026, 10, 8))
    if [r["id"] for r in plan["dated"]] != ["a", "b"] or plan["dated"][0]["days_from_as_of"] != -281:
        failures.append("dated rows must be ordered with signed day counts")
    if [r["id"] for r in plan["rolling"]] != ["m"]:
        failures.append("a rolling basis must stay a row without computed dates")
    text = ics(card, "https://example.org/")
    if "SUMMARY:Report\\, with commas\\; and more" not in text or text.count("BEGIN:VEVENT") != 2:
        failures.append("text must be escaped and only dated rows become events")
    if any(len(line.encode()) > 75 for line in text.split("\r\n")) or ics(card, "https://example.org/") != text:
        failures.append("lines must fold at 75 octets and the file must be stable")
    for bad in ("2026-13-01", "10/08/2026", None):
        try:
            parse_day(bad)
            failures.append(f"{bad!r} must be refused")
        except ValueError:
            pass
    return failures
