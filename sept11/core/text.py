"""Verbatim text handling: normalization, page splitting, quote location.

Matching is whitespace- and case-insensitive because several scanned PDFs
extract without spaces. A located quote means the normalized text matched; it
is never evidence that a claim is true (spec 08, "Evidence states").
"""
from __future__ import annotations

import re

PAGE_RE = re.compile(r"===== PAGE (\d+) =====")
_QUOTES = {"’": "'", "‘": "'", "“": '"', "”": '"'}


def norm(s: str) -> str:
    out = re.sub(r"\s+", "", s or "").lower()
    for fancy, plain in _QUOTES.items():
        out = out.replace(fancy, plain)
    return out


def split_pages(text: str) -> dict[int, str]:
    """Split captured document text on its page markers. Unmarked text is page 1."""
    parts = PAGE_RE.split(text)
    if len(parts) < 3:
        return {1: text}
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


_ELLIPSIS_RE = re.compile(r"\.{3}|…")


def _fragments_in_order(haystack: str, quote: str) -> bool:
    """A quote with an ellipsis is its fragments, each present, in order, after the last.

    "35,000 potential plaintiffs...10,000 would file a claim" marks omitted words; the
    fragments must all appear and must not be rearranged. A quote with no ellipsis is
    one fragment, matched whole.
    """
    fragments = [norm(f) for f in _ELLIPSIS_RE.split(quote)]
    fragments = [f for f in fragments if f]
    if not fragments:
        return False
    position = 0
    for fragment in fragments:
        found = haystack.find(fragment, position)
        if found < 0:
            return False
        position = found + len(fragment)
    return True


def quote_hits(text: str, quote: str, variants: list[str] | None = None) -> bool:
    haystack = norm(text)
    return any(_fragments_in_order(haystack, v) for v in [quote, *(variants or [])])


def truncate(text: str, limit: int) -> tuple[str, bool, int]:
    """Return (text, truncated, total_chars). Never truncates silently (spec 02 §3.3)."""
    total = len(text)
    if total <= limit:
        return text, False, total
    return text[:limit], True, total
