"""The commitment ledger (spec 09, "Data contract" and "Acceptance and delivery").

A ledger row is an announcement with captured evidence. The stages after it
(adopted line, contract, payment, delivery) are evidenced independently or
marked `not_linked`. Three refusals live here because each one is a way an
announcement quietly becomes a spending figure:

  * amounts are decimal strings, never floats, and no row or file carries a
    `total`, `sum` or `combined` field: rows with different fiscal periods
    are not comparable and none of them is expenditure;
  * two rows with the same amount in different periods stay two rows;
  * the announcement stage must be `located` with a claim id, so a row cannot
    exist without the quote that supports its amount;
  * an `observation` (another figure or fact on record about the same commitment,
    such as an agency's own account of what was allocated) carries its own claim id
    and sits beside the row; it never replaces or is added to the announced amount.
"""
from __future__ import annotations

import re

from .errors import IntegrityError
from .evidence import REVIEW

TOPICS = ("portal", "doi", "education")
STAGES = ("announcement", "adopted_line", "contract", "payment", "delivery")
STAGE_STATES = ("located", "not_linked", "observed", "partially_observed", "not_observed",
                "unable_to_check", "upcoming")
BASES = ("announcement", "adopted", "modified")
FORBIDDEN_KEYS = ("total", "sum", "combined", "aggregate")
_AMOUNT_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")
_PERIOD_RE = re.compile(r"^(FY[0-9]{4}|unresolved)$")


def _walk_keys(node, path: str, problems: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                problems.append(f"{path}.{key}: a ledger never carries a {key!r}; rows are not summed")
            _walk_keys(value, f"{path}.{key}", problems)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _walk_keys(value, f"{path}[{i}]", problems)


def problems(ledger: dict) -> list[str]:
    """Everything wrong with a ledger document. Empty means it can be served."""
    found: list[str] = []
    if not isinstance(ledger, dict):
        return ["ledger must be an object"]
    _walk_keys(ledger, "ledger", found)
    if list(ledger.get("stages") or []) != list(STAGES):
        found.append(f"ledger.stages must be {list(STAGES)}")
    rows = ledger.get("commitments")
    if not isinstance(rows, list) or not rows:
        return found + ["ledger.commitments must be a non-empty array"]
    seen: set[str] = set()
    for i, row in enumerate(rows):
        where = f"commitments[{i}]"
        if not isinstance(row, dict):
            found.append(f"{where}: not an object")
            continue
        cid = row.get("id")
        if not isinstance(cid, str) or not cid:
            found.append(f"{where}: missing id")
        elif cid in seen:
            found.append(f"{where}: duplicate id {cid!r}")
        else:
            seen.add(cid)
        if row.get("topic") not in TOPICS:
            found.append(f"{where}: topic must be one of {TOPICS}")
        amount = row.get("amount_decimal")
        if not isinstance(amount, str) or not _AMOUNT_RE.match(amount):
            found.append(f"{where}: amount_decimal must be a decimal string, got {amount!r}")
        if row.get("currency") != "USD":
            found.append(f"{where}: currency must be 'USD'")
        periods = row.get("fiscal_periods")
        if (not isinstance(periods, list) or not periods
                or any(not isinstance(p, str) or not _PERIOD_RE.match(p) for p in periods)):
            found.append(f"{where}: fiscal_periods must list FYyyyy values or 'unresolved'")
        if row.get("basis") not in BASES:
            found.append(f"{where}: basis must be one of {BASES}")
        if not isinstance(row.get("claim_id"), str) or not row.get("claim_id"):
            found.append(f"{where}: claim_id is required; an amount without a registered quote is not a row")
        review = row.get("review") or {}
        if review.get("status") not in REVIEW:
            found.append(f"{where}: review.status must be one of {REVIEW}")
        for j, obs in enumerate(row.get("observations") or []):
            if (not isinstance(obs, dict) or not isinstance(obs.get("text"), str) or not obs.get("text")
                    or not isinstance(obs.get("claim_id"), str) or not obs.get("claim_id")):
                found.append(f"{where}.observations[{j}]: needs `text` and `claim_id`; an observation without a "
                             "registered quote is not on record")
        stages = row.get("stages")
        if not isinstance(stages, dict) or set(stages) != set(STAGES):
            found.append(f"{where}: stages must carry exactly {list(STAGES)}")
            continue
        for name in STAGES:
            stage = stages[name]
            if not isinstance(stage, dict) or stage.get("state") not in STAGE_STATES:
                found.append(f"{where}.stages.{name}: state must be one of {STAGE_STATES}")
                continue
            if name == "announcement":
                if stage["state"] != "located" or stage.get("claim_id") != row.get("claim_id"):
                    found.append(f"{where}.stages.announcement: must be located with the row's claim_id")
            elif stage["state"] == "not_linked" and not stage.get("note"):
                found.append(f"{where}.stages.{name}: a not_linked stage says why in `note`")
    return found


def validate(ledger: dict) -> dict:
    found = problems(ledger)
    if found:
        raise IntegrityError("commitment ledger rejected: " + "; ".join(found[:5]))
    return ledger


def select(ledger: dict, topic: str = "all") -> list[dict]:
    """Rows for one topic, or all rows. Never a total: the caller gets rows, one by one."""
    if topic not in TOPICS + ("all",):
        raise ValueError(f"topic must be one of {TOPICS + ('all',)}")
    rows = ledger["commitments"]
    return [dict(r) for r in rows if topic == "all" or r.get("topic") == topic]


def periods_comparable(rows: list[dict]) -> bool:
    """True only when every row names the same single resolved fiscal period."""
    periods = {tuple(r.get("fiscal_periods") or []) for r in rows}
    return len(periods) == 1 and "unresolved" not in next(iter(periods)) and len(next(iter(periods))) == 1
