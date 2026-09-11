"""The citation unit: Bates number + one-based PDF page (decision D2).

Two identifiers are deliberately kept apart (spec 08, "Record contract"):

    bates   the document-start Bates ID, which names the PDF file
    stamp   the Bates value printed in the footer of the cited page

A stamp is read off the page or reported missing. It is never computed from
`bates + page - 1`; inserted, duplicated and unstamped pages are real.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

CONTENT_PATH = "/apps/content/September11_MD/{bates}.pdf"
# Citations always point at the City's public front door, even when a request went to the
# Mindbreeze tenant behind it: the City is the canonical host (spec 10).
CANONICAL_BASE = "https://sept11documents.cityofnewyork.us"
# The footer stamp as OCR reads it. "NYC-VVTC" is the text layer's recurring misread of
# "NYC-WTC" (two Vs for a W); accepting it reads the page, which is not the same as
# computing a stamp from the document ID.
BATES_RE = re.compile(r"NYC-(?:W|VV)TC[ _]?(\d{6,})")
_SUFFIXES = (".pdf_MD", "_MD", ".pdf", ".md")


def normalize_bates(value: str) -> str:
    """'NYC-WTC0003/SPDF/PDF001/NYC-WTC_000058159.pdf' -> 'NYC-WTC_000058159'."""
    v = str(value).strip().split("/")[-1]
    for suffix in _SUFFIXES:
        if v.endswith(suffix):
            v = v[: -len(suffix)]
    if not v.startswith("NYC-WTC_") or not v[8:].isdigit():
        raise ValueError(f"not a Bates number: {value!r}")
    return v


def pdf_url(bates: str, base: str = CANONICAL_BASE) -> str:
    return base.rstrip("/") + CONTENT_PATH.format(bates=normalize_bates(bates))


def stamps_on(page_text: str) -> list[str]:
    """Bates stamps printed on a page, in order of appearance, deduplicated."""
    seen, out = set(), []
    for digits in BATES_RE.findall(page_text or ""):
        stamp = f"NYC-WTC_{digits}"
        if stamp not in seen:
            seen.add(stamp)
            out.append(stamp)
    return out


@dataclass(frozen=True)
class Citation:
    bates: str
    page: int
    pdf_url: str
    stamp: str | None = None
    # matched: the printed stamp was read off this page; unverified_stamp: none legible.
    stamp_status: str = "unverified_stamp"
    pdf_sha256: str | None = None
    # The portal's per-document HTML view URL is not documented; only the PDF URL is verified.
    portal_url: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def build(bates: str, page: int, base: str = CANONICAL_BASE, page_text: str | None = None,
          pdf_sha256: str | None = None) -> Citation:
    bates = normalize_bates(bates)
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError(f"page must be a positive integer, got {page!r}")
    stamp, status = None, "unverified_stamp"
    if page_text is not None:
        found = stamps_on(page_text)
        if len(found) == 1:
            stamp, status = found[0], "matched"
        elif len(found) > 1:
            stamp, status = None, "ambiguous_stamp"
    return Citation(bates=bates, page=page, pdf_url=pdf_url(bates, base),
                    stamp=stamp, stamp_status=status, pdf_sha256=pdf_sha256)
