"""PII stop rule (spec 05 §PII; spec 08 "PII controls").

The screen reports *kinds* and counts. It never returns, logs or stores the
matched text: copying an SSN into a tool result or a log is the harm the screen
exists to prevent. A negative screen is not approval for publication; page
images and OCR still need human review.

Bates numbers and ordinary dates must not fire the screen; a run of nine digits
inside 'NYC-WTC_000138296' looks like an unformatted SSN, so Bates tokens are
masked before any pattern runs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .citations import BATES_RE

PORTAL_PII_FORM = ("https://sept11documents.cityofnewyork.us — 'Notify Us About "
                   "Personal Information', which asks for the Bates page")

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")),
    ("phone", re.compile(r"(?<!\d)(?:\(\d{3}\)\s?|\d{3}[-.\s])\d{3}[-.\s]\d{4}(?!\d)")),
]
# A bare date is not PII; a date introduced as a birth date is.
_DOB_RE = re.compile(
    r"(?:d\.?o\.?b\.?|date\s+of\s+birth|birth\s*date|born(?:\s+on)?)\s*[:\-]?\s*"
    r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE)
# 'SSN: 123456789' — only with an explicit cue, so Bates-like digit runs stay quiet.
_CUED_SSN_RE = re.compile(r"(?:ssn|social\s+security(?:\s+(?:no\.?|number|#))?)\s*[:\-#]?\s*\d{9}\b",
                          re.IGNORECASE)


@dataclass(frozen=True)
class Screen:
    suspect: bool
    kinds: tuple[str, ...]
    count: int

    def as_dict(self) -> dict:
        out = {"pii_suspect": self.suspect, "pii_kinds": list(self.kinds), "pii_matches": self.count}
        if self.suspect:
            out["pii_report_form"] = PORTAL_PII_FORM
        return out


def screen(text: str) -> Screen:
    """Screen page or snippet text. Returns kinds and counts, never the matched text."""
    if not text:
        return Screen(False, (), 0)
    masked = BATES_RE.sub("BATES", text)
    kinds: list[str] = []
    total = 0
    for kind, pattern in _PATTERNS:
        hits = len(pattern.findall(masked))
        if hits:
            kinds.append(kind)
            total += hits
    for kind, pattern in (("dob", _DOB_RE), ("ssn", _CUED_SSN_RE)):
        hits = len(pattern.findall(masked))
        if hits:
            if kind not in kinds:
                kinds.append(kind)
            total += hits
    return Screen(bool(kinds), tuple(kinds), total)
