"""Street addresses in the City's folder labels (spec 02 §3.10, label side only).

DEP's hard-copy boxes file asbestos and air-monitoring paperwork by building, so a
folder label often reads like `10 HANOVER SQUARE  Block: 31 Lot: 1 BIN: 1000859
110-124 Pearl Street 76-88 Water Street`. This module reads those labels as text:
it normalizes street names, reads house numbers and ranges, and pulls out BIN,
block and lot where the label prints them. It does not geocode, and it decides
nothing about exposure zones; a label match says where the City filed paper.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Suffixes and directionals as they appear in the labels, mapped to one spelling.
_WORDS = {
    "ST": "STREET", "STR": "STREET", "STREET": "STREET",
    "AVE": "AVENUE", "AV": "AVENUE", "AVENUE": "AVENUE",
    "PL": "PLACE", "PLACE": "PLACE", "SQ": "SQUARE", "SQUARE": "SQUARE",
    "PLZ": "PLAZA", "PLAZA": "PLAZA", "BLVD": "BOULEVARD", "DR": "DRIVE",
    "TER": "TERRACE", "LN": "LANE", "RD": "ROAD", "HWY": "HIGHWAY", "SLIP": "SLIP",
    "BWAY": "BROADWAY", "BDWY": "BROADWAY", "BROADWAY": "BROADWAY",
    "W": "WEST", "E": "EAST", "N": "NORTH", "S": "SOUTH",
}
_ORDINAL = re.compile(r"\b(\d+)(ST|ND|RD|TH)\b")
_TOKEN = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)?")
_NUMBER = re.compile(r"^(\d+)(?:-(\d+))?[A-Z]?$")
_BIN = re.compile(r"\bBIN[:;#\s]*\s*(\d{7})\b")
_BLOCK = re.compile(r"\bBLOCK[:;#\s]*\s*(\d{1,5})\b")
_LOT = re.compile(r"\bLOT[:;#\s]*\s*(\d{1,4})\b")
# Many DEP labels print "BIN, block/lot" without the words: `15 JOHN STREET 1001217, 79/14`.
_BIN_PAIR = re.compile(r"\b(\d{7}),\s*(\d{1,5})/(\d{1,4})\b")

MAX_ADDRESS_CHARS = 80


def normalize(text: str) -> list[str]:
    """Upper-case tokens with suffixes, directionals and ordinals in one spelling."""
    text = (text or "").upper().replace("B'WAY", "BWAY").replace("’", "'").replace("'", "")
    text = _ORDINAL.sub(r"\1", text)
    return [_WORDS.get(token, token) for token in _TOKEN.findall(text)]


@dataclass(frozen=True)
class Query:
    number: int | None
    street: tuple[str, ...]
    bin: str | None

    def describe(self) -> str:
        if self.bin:
            return f"BIN {self.bin}"
        return " ".join(([str(self.number)] if self.number is not None else []) + list(self.street))


def parse_query(address: str) -> Query:
    """`120 Broadway` -> number 120, street (BROADWAY,). A bare seven-digit number is a BIN."""
    if not isinstance(address, str) or not address.strip():
        raise ValueError("address must be a non-empty string, e.g. '15 John Street' or a seven-digit BIN")
    if len(address) > MAX_ADDRESS_CHARS:
        raise ValueError(f"address is {len(address)} characters; the limit is {MAX_ADDRESS_CHARS}")
    raw = address.strip()
    bin_only = re.fullmatch(r"(?:BIN[:\s#]*)?(\d{7})", raw.upper())
    if bin_only:
        return Query(number=None, street=(), bin=bin_only.group(1))
    tokens = normalize(raw)
    number = None
    if tokens and _NUMBER.match(tokens[0]):
        number = int(_NUMBER.match(tokens[0]).group(1))
        tokens = tokens[1:]
    street = tuple(t for t in tokens if t not in ("NEW", "YORK", "NY", "NYC", "MANHATTAN"))
    if not street:
        raise ValueError("give a street name, e.g. 'John Street', '15 John Street' or 'Stuyvesant'")
    return Query(number=number, street=street, bin=None)


def identifiers(label: str) -> dict:
    """BIN, block and lot where the label prints them; never inferred."""
    upper = (label or "").upper()
    pair = _BIN_PAIR.search(upper)
    return {"bins": sorted(set(_BIN.findall(upper)) | ({pair.group(1)} if pair else set())),
            "block": (_BLOCK.search(upper) or [None, None])[1] or (pair.group(2) if pair else None),
            "lot": (_LOT.search(upper) or [None, None])[1] or (pair.group(3) if pair else None)}


def _numbers_before(tokens: list[str], at: int) -> list[tuple[int, int]]:
    """House numbers printed immediately before a street name, as inclusive ranges."""
    spans = []
    i = at - 1
    while i >= 0:
        match = _NUMBER.match(tokens[i])
        if not match:
            break
        low = int(match.group(1))
        high = int(match.group(2)) if match.group(2) else low
        spans.append((min(low, high), max(low, high)))
        i -= 1
    return spans


def match(label: str, query: Query) -> dict | None:
    """How a label matches, or None. A number must sit before the street it names."""
    if query.bin:
        ids = identifiers(label)
        return {"by": "bin", "printed_numbers": []} if query.bin in ids["bins"] else None
    tokens = normalize(label)
    width = len(query.street)
    starts = [i for i in range(len(tokens) - width + 1) if tuple(tokens[i:i + width]) == query.street]
    if not starts:
        return None
    if query.number is None:
        return {"by": "street", "printed_numbers": []}
    for start in starts:
        spans = _numbers_before(tokens, start)
        if any(low <= query.number <= high for low, high in spans):
            # A range covers both sides of a street; the label alone cannot say which side.
            return {"by": "number_and_street", "printed_numbers": [f"{a}" if a == b else f"{a}-{b}" for a, b in spans]}
    return None


def selftest() -> list[str]:
    failures = []
    label = ("1 HANOVER SQUARE  Block; 29 Lot: 7502 BIN: 1000841 64 Stone Street 2 Hanover Square "
             "60-64 Stone St")
    q = parse_query("62 Stone St")
    if not match(label, q) or match(label, parse_query("70 Stone Street")):
        failures.append("a range in a label must cover numbers inside it only")
    if not match(label, parse_query("1000841")) or identifiers(label)["block"] != "29":
        failures.append("BIN and block must be read from the label")
    if identifiers("15 JOHN STREET 1001217, 79/14") != {"bins": ["1001217"], "block": "79", "lot": "14"}:
        failures.append("the 'BIN, block/lot' label form must be read")
    if match("115 BROADWAY", parse_query("15 Broadway")):
        failures.append("15 must not match 115")
    if not match("120 B'WAY", parse_query("120 Broadway")) or not match("2 W 14TH ST", parse_query("2 West 14 Street")):
        failures.append("abbreviations and ordinals must normalize")
    if not match("STUYVESANT HIGH SCHOOL 345 CHAMBERS", parse_query("Stuyvesant")):
        failures.append("a bare name must match by street words")
    for bad in ("", "x" * 200, "15"):
        try:
            parse_query(bad)
            failures.append(f"{bad[:10]!r} must be refused")
        except ValueError:
            pass
    return failures
