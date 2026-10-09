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
_NUMBER = re.compile(r"^(\d+)(?:-(\d+))?([A-Z]?)$")
# Labels print the BIN as `BIN: 1000859`, `BIN# 1000859`, `BN# 1001215`, `BN # 1079039` or
# `B# 1083350`; a bare B counts only with its #, so `BID # 1234567` is not read as a BIN.
_BIN_WORD = re.compile(r"\bBI?N\b|\bB\s?#")
_BIN = re.compile(r"\b(?:BI?N[:;#\s]*|B\s?#\s*)(\d{7})\b")
# A second BIN can follow the first after a slash: `BN# 1001269 / 1001268`.
_BIN_MORE = re.compile(r"\b(?:BI?N[:;#\s]*|B\s?#\s*)\d{7}((?:\s*/\s*\d{7}(?!\d))+)")
# Block is also printed `Bl. 46`, or `BI 69` where the scan reads l as I; the short forms count only
# when a lot follows. `69Lot.` runs the words together, so LOT is bounded by letters only.
_BLOCK = re.compile(r"\b(?:BLOCK|B[LI]\.?(?=\s*\d{1,5}[\s.,;:]*LOT))[.:;#\s]*(\d{1,5})(?!\d)")
_LOT = re.compile(r"(?<![A-Z])LOT[.:;#\s]*(\d{1,4})(?!\d)")
# Some labels print the BIN straight after the lot: `Bl. 46 Lot 9/1001025`, `Lot: 8 1000872`.
_BIN_AFTER_LOT = re.compile(r"(?<![A-Z])LOT[.:;#\s]*\d{1,4}(?:\s*/\s*|\s+)(\d{7})(?!\d)")
# Many DEP labels print "BIN, block/lot" without the words: `15 JOHN STREET 1001217, 79/14`.
_BIN_PAIR = re.compile(r"\b(\d{7}),\s*(\d{1,5})/(\d{1,4})\b")
# Some print a space for the comma: `17 JOHN STREET 1001216 79/10`. Read only in labels without the
# word BIN or BN, where `BIN: 1001396 26/42 PARK PL` shows the pair can be a house-number range instead.
_BIN_PAIR_SPACED = re.compile(r"\b(\d{7})\s+(\d{1,5})/(\d{1,4})\b(?![/\d])")
# A few print the pair without its slash (`113 NASSAU STREET 1001256, 90117`); the BIN before the
# comma is read and the run-together block and lot are left unread.
_BIN_COMMA = re.compile(r"\b(\d{7}),\s*\d{2,9}\b(?!/)")
# `190 BROADWAY 1801241`: a whole label of house number, street and a trailing Manhattan BIN. Anchored
# to the full label so project and contract numbers (`PW 3261346`, `BID # 9900814`) stay unread.
_BIN_TRAILING = re.compile(r"^\d+[A-Z]?(?:-\d+)?\s+[A-Z][A-Z .']*?\s(1\d{6})$")
# `345 CHAMBERS STREET; 16/1`: a block/lot pair printed straight after the street word. Dates
# (`9/19/01`), half numbers (`86 1/2`) and ranges (`161/167 William`) never follow a street word.
_STREET_WORD = "|".join(sorted((k for k, v in _WORDS.items() if v in {
    "STREET", "AVENUE", "PLACE", "SQUARE", "PLAZA", "BOULEVARD", "DRIVE", "TERRACE", "LANE", "ROAD",
    "HIGHWAY", "SLIP", "BROADWAY"}), key=len, reverse=True))
_PAIR_AFTER_STREET = re.compile(rf"\b(?:{_STREET_WORD})\.?[;,]?\s+(?:(\d{{6,7}}),\s*)?(\d{{1,5}})/(\d{{1,4}})(?![/\d])")

# Street-type words; a query made only of these names no street ("West Street" is a street).
_SUFFIXES = frozenset({"STREET", "AVENUE", "PLACE", "SQUARE", "PLAZA", "BOULEVARD", "DRIVE",
                       "TERRACE", "LANE", "ROAD", "HIGHWAY", "SLIP"})
# City, state and ZIP printed after an address: `15 John Street, New York, NY 10038`.
_CITY_TAIL = frozenset({"NY", "NYC", "MANHATTAN"})
_ZIP = re.compile(r"^\d{5}$")

MAX_ADDRESS_CHARS = 80


def _upper(text: str) -> str:
    return (text or "").upper().replace("B'WAY", "BWAY").replace("’", "'").replace("'", "")


def normalize(text: str) -> list[str]:
    """Upper-case tokens with suffixes, directionals and ordinals in one spelling."""
    text = _ORDINAL.sub(r"\1", _upper(text))
    return [_WORDS.get(token, token) for token in _TOKEN.findall(text)]


def _strip_city_tail(tokens: list[str]) -> list[str]:
    """Drop a trailing city, state or ZIP; `1 New York Plaza` keeps its street words."""
    tokens = list(tokens)
    while tokens:
        if tokens[-1] in _CITY_TAIL or _ZIP.match(tokens[-1]):
            tokens.pop()
        elif tokens[-2:] == ["NEW", "YORK"]:
            del tokens[-2:]
        else:
            break
    return tokens


@dataclass(frozen=True)
class Query:
    number: int | None
    street: tuple[str, ...]
    bin: str | None
    suffix: str = ""  # `19A South Street`: the A is part of the address

    def describe(self) -> str:
        if self.bin:
            return f"BIN {self.bin}"
        return " ".join(([f"{self.number}{self.suffix}"] if self.number is not None else []) + list(self.street))


def parse_query(address: str) -> Query:
    """`120 Broadway` -> number 120, street (BROADWAY,). A bare seven-digit number is a BIN."""
    if not isinstance(address, str) or not address.strip():
        raise ValueError("address must be a non-empty string, e.g. '15 John Street' or a seven-digit BIN")
    if len(address) > MAX_ADDRESS_CHARS:
        raise ValueError(f"address is {len(address)} characters; the limit is {MAX_ADDRESS_CHARS}")
    raw = address.strip()
    bin_only = re.fullmatch(r"(?:BI?N[:\s#]*|B\s?#\s*)?(\d{7})", raw.upper())
    if bin_only:
        return Query(number=None, street=(), bin=bin_only.group(1))
    tokens = normalize(raw)
    number = None
    # Read the house number before ordinals are normalized: `14th Street` has none.
    first = _TOKEN.findall(_upper(raw))[:1]
    if tokens and first and _NUMBER.match(first[0]) and not _ORDINAL.fullmatch(first[0]):
        read = _NUMBER.match(tokens[0])
        number, suffix = int(read.group(1)), "" if read.group(2) else read.group(3)
        tokens = tokens[1:]
    street = tuple(_strip_city_tail(tokens))
    if not street or set(street) <= _SUFFIXES:
        raise ValueError("give a street name, e.g. 'John Street', '15 John Street' or 'Stuyvesant'")
    return Query(number=number, street=street, bin=None, suffix=suffix if number is not None else "")


def _identifier_matches(upper: str) -> tuple[list, list]:
    """Every BIN, block and lot match in an upper-cased label, and the `BIN, block/lot` pairs among them."""
    pairs = list(_BIN_PAIR.finditer(upper))
    if not pairs and not _BIN_WORD.search(upper):
        pairs = list(_BIN_PAIR_SPACED.finditer(upper))
    found = pairs + [m for rx in (_BIN, _BIN_MORE, _BIN_AFTER_LOT, _BLOCK, _LOT, _BIN_TRAILING, _PAIR_AFTER_STREET)
                     for m in rx.finditer(upper)]
    if not pairs:
        found += list(_BIN_COMMA.finditer(upper))
    return found, pairs


def identifiers(label: str) -> dict:
    """BIN, block and lot where the label prints them; never inferred."""
    upper = (label or "").upper()
    _, pairs = _identifier_matches(upper)
    more = [b for m in _BIN_MORE.finditer(upper) for b in re.findall(r"\d{7}", m.group(1))]
    after_street = _PAIR_AFTER_STREET.search(upper)
    first = (pairs[0] if pairs else after_street).group(2, 3) if pairs or after_street else None
    return {"bins": sorted(set(_BIN.findall(upper)) | set(more) | set(_BIN_AFTER_LOT.findall(upper))
                           | (set() if pairs else set(_BIN_COMMA.findall(upper)))
                           | set(_BIN_TRAILING.findall(upper)) | {m.group(1) for m in pairs}),
            "block": (_BLOCK.search(upper) or [None, None])[1] or (first[0] if first else None),
            "lot": (_LOT.search(upper) or [None, None])[1] or (first[1] if first else None)}


def _street_text(label: str) -> str:
    """The label with its BIN, block and lot blanked, so a house-number scan stops at them.

    Each match becomes ` X###…` of the same length (every match is at least three characters): the
    space keeps `69LOT` from reading as house number 69X, and the X token ends the scan.
    """
    upper = (label or "").upper()
    text = list(upper)
    for found in _identifier_matches(upper)[0]:
        # A match that begins with its street word or house number blanks only the numbers after it.
        start, end = found.span()
        if found.re is _BIN_TRAILING:
            start = found.start(1)
        elif found.re is _PAIR_AFTER_STREET:
            start = found.start(1) if found.group(1) else found.start(2)
        text[start:end] = " X" + "#" * (end - start - 2)
    return "".join(text)


def _numbers_before(tokens: list[str], at: int) -> list[tuple[int, int, str]]:
    """House numbers printed immediately before a street name: inclusive ranges, and the letter of a
    single number such as `19A` (empty for a range or a plain number)."""
    spans = []
    i = at - 1
    while i >= 0:
        match = _NUMBER.match(tokens[i])
        if not match:
            break
        low = int(match.group(1))
        high = int(match.group(2)) if match.group(2) else low
        spans.append((min(low, high), max(low, high), "" if match.group(2) else match.group(3)))
        i -= 1
    return spans


def _covers(span: tuple[int, int, str], query: Query) -> bool:
    """A range covers the plain numbers inside it; a lettered number matches only the same letter.

    `323A Greenwich Street` and `190A Duane Street` are lots of their own beside 323 and 188-190.
    """
    low, high, letter = span
    if low != high:
        return not query.suffix and low <= query.number <= high
    return query.number == low and query.suffix == letter


def match(label: str, query: Query) -> dict | None:
    """How a label matches, or None. A number must sit before the street it names."""
    if query.bin:
        ids = identifiers(label)
        return {"by": "bin", "printed_numbers": []} if query.bin in ids["bins"] else None
    tokens = normalize(_street_text(label))
    width = len(query.street)
    starts = [i for i in range(len(tokens) - width + 1) if tuple(tokens[i:i + width]) == query.street]
    if not starts:
        return None
    if query.number is None:
        return {"by": "street", "printed_numbers": []}
    for start in starts:
        spans = _numbers_before(tokens, start)
        if any(_covers(span, query) for span in spans):
            # A range covers both sides of a street; the label alone cannot say which side.
            return {"by": "number_and_street",
                    "printed_numbers": [f"{a}{c}" if a == b else f"{a}-{b}" for a, b, c in spans]}
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
    if identifiers("17 JOHN STREET 1001216 79/10") != {"bins": ["1001216"], "block": "79", "lot": "10"}:
        failures.append("the 'BIN block/lot' label form without a comma must be read")
    if identifiers("29 JOHN STREET BN# 1001215")["bins"] != ["1001215"] or identifiers("4-6 LIBERTY PLACE BN # 1079039")["bins"] != ["1079039"]:
        failures.append("the 'BN#' and 'BN #' label forms must be read as a BIN")
    if identifiers("333 PEARL STREET B# 1083350")["bins"] != ["1083350"] or identifiers("FURNITURE BID # 1234567")["bins"]:
        failures.append("'B# 1083350' must be read as a BIN and 'BID # 1234567' must not")
    if parse_query("BN# 1001215").bin != "1001215":
        failures.append("a query written 'BN# 1001215' must be read as a BIN")
    if identifiers("BN: 1001396 26/42 PARK PL")["block"] is not None:
        failures.append("a range after a BIN labelled BN must not be read as block and lot")
    for label, want in (("14 WALL STREET; Bl. 46 Lot 9/1001025", (["1001025"], "46", "9")),
                        ("90 JOHN STREET bi. 76Lot. 11/1001180", (["1001180"], "76", "11")),
                        ("110 WALL STREET; Block: 37 Lot: 8 1000872", (["1000872"], "37", "8"))):
        got = identifiers(label)
        if (got["bins"], got["block"], got["lot"]) != want:
            failures.append(f"block, lot and BIN must be read from {label!r}, got {got}")
    if identifiers("52 CHAMBERS STREET 1079146, 122/1 52 CHAMBERS STREET 1079147, 122/1")["bins"] != ["1079146", "1079147"]:
        failures.append("every 'BIN, block/lot' pair in a label must be read")
    if identifiers("161/167 William Street; BN# 1001269 / 1001268")["bins"] != ["1001268", "1001269"]:
        failures.append("a second BIN after a slash must be read")
    hit = match("50 BROADWAY; Block: 22 Lot: 28 BIN: 1000813 47 New Street", parse_query("47 New Street"))
    if not hit or hit["printed_numbers"] != ["47"]:
        failures.append(f"a BIN before an alternate address is not a house number, got {hit}")
    hit = match("110 WALL STREET; Block: 37 Lot: 8 1000872 119-127 Front Street", parse_query("120 Front Street"))
    if not hit or hit["printed_numbers"] != ["119-127"]:
        failures.append(f"a lot and BIN before an address are not house numbers, got {hit}")
    hit = match("112 JOHN STREET BI. 69Lot. 54/1001131 2 GOLD STREET", parse_query("2 Gold Street"))
    if not hit or hit["printed_numbers"] != ["2"]:
        failures.append(f"a run-together block and lot are not house numbers, got {hit}")
    if identifiers("190 BROADWAY 1801241")["bins"] != ["1801241"] or identifiers("Capital Project: PW 3261346 Approved")["bins"]:
        failures.append("a trailing BIN after an address must be read and a project number must not")
    for label, pair in (("345 CHAMBERS STREET; 16/1", ("16", "1")), ("51 HARRISON ST 142/50", ("142", "50")),
                        ("320 ALBANY STREET 000301, 16/7502", ("16", "7502"))):
        got = identifiers(label)
        if (got["block"], got["lot"]) != pair:
            failures.append(f"the block/lot pair after the street in {label!r} must be read, got {got}")
    for label in ("508 9/19/01 Extract 9/27 Analysis", "86 1/2 Nassau St", "161/167 William Street"):
        if identifiers(label)["block"] is not None:
            failures.append(f"{label!r} prints a date, a half number or a range, and no block")
    if match("320 ALBANY STREET 000301, 16/7502 1 X STREET", parse_query("301 X Street")):
        failures.append("the number printed before a block/lot pair is not a house number")
    if not match("345 CHAMBERS STREET; 16/1", parse_query("345 Chambers Street")):
        failures.append("blanking a block/lot pair must keep the street it follows")
    suffixed = "19A SOUTH STREET"
    if match(suffixed, parse_query("19 South Street")) or match(suffixed, parse_query("19B South Street")):
        failures.append("19 and 19B South Street must not match a label printing 19A")
    hit = match(suffixed, parse_query("19A South Street"))
    if not hit or hit["printed_numbers"] != ["19A"] or parse_query("19A South Street").describe() != "19A SOUTH STREET":
        failures.append(f"19A South Street must match 19A and report it with its letter, got {hit}")
    if match("188-190 DUANE STREET", parse_query("190A Duane Street")) or not match("188-190 DUANE STREET", parse_query("189 Duane Street")):
        failures.append("a printed range covers its plain numbers and no lettered number")
    got = identifiers("113 NASSAU STREET 1001256, 90117")
    if (got["bins"], got["block"], got["lot"]) != (["1001256"], None, None):
        failures.append(f"a BIN before an unslashed block and lot must be read, the pair left unread, got {got}")
    if identifiers("BLDG 4 BIBLE 12")["block"] is not None or identifiers("PILOT 12")["lot"] is not None:
        failures.append("a short block label needs a lot after it, and LOT inside a word is not a lot")
    if identifiers("BIN: 1001396 26/42 PARK PL")["block"] is not None:
        failures.append("a range after a labelled BIN must not be read as block and lot")
    if identifiers("REPORT 1001216 10/16/01")["bins"]:
        failures.append("a date after a seven-digit number must not be read as block and lot")
    if match("115 BROADWAY", parse_query("15 Broadway")):
        failures.append("15 must not match 115")
    if not match("120 B'WAY", parse_query("120 Broadway")) or not match("2 W 14TH ST", parse_query("2 West 14 Street")):
        failures.append("abbreviations and ordinals must normalize")
    if not match("STUYVESANT HIGH SCHOOL 345 CHAMBERS", parse_query("Stuyvesant")):
        failures.append("a bare name must match by street words")
    if not match("BIN: 1000813 47 New Street", parse_query("47 New Street")):
        failures.append("NEW in a street name must be kept")
    if not match("4 NEW YORK PLAZA", parse_query("4 New York Plaza")):
        failures.append("NEW YORK inside a street name must be kept")
    if parse_query("15 John Street, New York, NY 10038").describe() != "15 JOHN STREET":
        failures.append("a trailing city, state and ZIP must be dropped")
    q = parse_query("14th Street")
    if q.number is not None or q.street != ("14", "STREET"):
        failures.append("an ordinal street must not be read as a house number")
    for bad in ("", "x" * 200, "15", "Street", "12 Avenue", "New York, NY"):
        try:
            parse_query(bad)
            failures.append(f"{bad[:10]!r} must be refused")
        except ValueError:
            pass
    return failures
