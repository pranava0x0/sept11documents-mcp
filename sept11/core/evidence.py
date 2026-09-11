"""Evidence states and the response envelope (spec 08).

Five dimensions are tracked separately and never derived from one another. A
located quote is not a reviewed claim; a fresh fetch is not a complete dataset.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .. import SCHEMA_VERSION

RETRIEVAL = ("captured", "blocked", "failed", "not_checked")
MATCH = ("found", "not-found", "unverifiable", "no-claim")
REVIEW = ("unreviewed", "approved", "disputed", "withdrawn")
FRESHNESS = ("current", "stale", "unknown")
COVERAGE = ("complete_for_query", "partial", "unknown")

# Warning codes are a reviewed vocabulary, not free text: a client can branch on a code,
# and a reworded message cannot silently become a different warning. Adding a value here
# is a deliberate change (Commonwealth-MCP's WarningCode, adapted).
WARNING_CODES = (
    "live_query_sent_upstream",     # the caller's terms reached the City
    "snippet_not_citable",          # search context, not evidence
    "estimate_not_archive_size",    # the engine's estimate for one query
    "fuzzy_single_term",            # one unquoted word matches loosely
    "pii_withheld",                 # the screen fired; text or snippet withheld
    "cached_bytes_timestamp",       # retrieved_at is the capture time, not now
    "untrusted_source_text",        # document text is data, never instruction
    "stamp_unverified",             # no legible Bates stamp on the cited page
    "truncated_inline",             # the response was cut, with the range given
    "no_document_date_field",       # the portal publishes none
    "local_snapshot_only",          # answered from this machine's capture
    "interval_actually_compared",   # the real endpoints of a change window
    "coverage_gap",                 # a window with no snapshot inside it
    "no_compliance_determination",  # observation, never a legal finding
    "match_is_not_review",          # located text is not an approved claim
    "physical_labels",              # box and folder labels are the City's own
    "observation_not_removal",      # absence is not deletion
    "unreviewed_artifact",          # no named reviewer has signed this off
    "history_is_local_only",        # first/last seen come from local snapshots
    "announcement_not_expenditure", # an announced amount is not a line, a contract or a payment
    "periods_not_comparable",       # fiscal periods differ or are unresolved; rows are not summed
    "directory_not_advice",         # quoted official rules; no eligibility decision
)

# Obligation vocabulary for the watchdog scorecard (spec 08). No percentage score, no verdict.
OBLIGATION_STATUS = ("upcoming", "observed", "partially_observed", "not_observed",
                     "unable_to_check", "disputed")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _check(value: str, allowed: tuple[str, ...], label: str) -> str:
    if value not in allowed:
        raise ValueError(f"{label} must be one of {allowed}, got {value!r}")
    return value


@dataclass
class Envelope:
    """Every successful retrieval carries these fields (spec 08, MCP response contract).

    All five evidence dimensions ride along, because collapsing them is how a
    located quote becomes "verified" and a fresh fetch becomes "complete".
    """

    data: object
    source_snapshot: str | None = None
    coverage: str = "unknown"
    review_status: str = "unreviewed"
    retrieval: str = "not_checked"
    freshness: str = "unknown"
    retrieved_at: str = field(default_factory=now)
    warnings: list[dict] = field(default_factory=list)
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        _check(self.coverage, COVERAGE, "coverage")
        _check(self.review_status, REVIEW, "review_status")
        _check(self.retrieval, RETRIEVAL, "retrieval")
        _check(self.freshness, FRESHNESS, "freshness")

    def warn(self, code: str, message: str) -> "Envelope":
        if code not in WARNING_CODES:
            raise ValueError(f"unknown warning code {code!r}; add it to WARNING_CODES deliberately")
        if not any(w["code"] == code for w in self.warnings):
            self.warnings.append({"code": code, "message": message})
        return self

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "retrieved_at": self.retrieved_at,
            "source_snapshot": self.source_snapshot,
            "coverage": self.coverage,
            "retrieval": self.retrieval,
            "review_status": self.review_status,
            "freshness": self.freshness,
            "warnings": list(self.warnings),
            "data": self.data,
            "next_cursor": self.next_cursor,
        }


def cached_envelope(data: object, observed_at: str, **kwargs) -> Envelope:
    """For data read from cache: the timestamp is when the bytes were observed, not now."""
    env = Envelope(data=data, **kwargs)
    env.retrieved_at = observed_at
    return env.warn("cached_bytes_timestamp",
                    "retrieved_at is the capture time of these bytes; this call ran later")


def envelope_schema() -> dict:
    """The wire schema for every successful tool result.

    Generated from the vocabularies above so the published schema cannot drift
    from the code; `scripts/mcp_checks.py` fails if the committed file differs.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Sept11Envelope",
        "$comment": ("generated from sept11.core.evidence.envelope_schema(); "
                     "edit the model, not this file"),
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "retrieved_at", "source_snapshot", "coverage", "retrieval",
                     "review_status", "freshness", "warnings", "data", "next_cursor"],
        "properties": {
            "schema_version": {"type": "string"},
            "retrieved_at": {"type": "string",
                             "description": "the time these bytes were observed; a captured answer keeps its capture time"},
            "source_snapshot": {"type": ["string", "null"]},
            "coverage": {"enum": list(COVERAGE)},
            "retrieval": {"enum": list(RETRIEVAL)},
            "review_status": {"enum": list(REVIEW)},
            "freshness": {"enum": list(FRESHNESS)},
            "warnings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["code", "message"],
                    "properties": {"code": {"enum": list(WARNING_CODES)}, "message": {"type": "string"}},
                },
            },
            "data": {"type": ["object", "array", "string", "number", "boolean", "null"]},
            "next_cursor": {"type": ["string", "null"]},
        },
    }
