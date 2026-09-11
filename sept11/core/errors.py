"""Structured failures. Every tool error reaches a client as {code, message, retry_after?}."""
from __future__ import annotations


class Sept11Error(RuntimeError):
    """Base for every failure this toolkit reports to a caller."""

    code = "internal_error"

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after

    def payload(self) -> dict:
        out: dict = {"code": self.code, "message": str(self)}
        if self.retry_after is not None:
            out["retry_after"] = round(self.retry_after, 3)
        return out


class InputError(Sept11Error):
    """The caller's arguments are outside the documented bounds."""

    code = "invalid_input"


class UpstreamError(Sept11Error):
    """The City's portal failed or refused; the caller may retry later."""

    code = "upstream_unavailable"


class BudgetError(Sept11Error):
    """A rate or byte budget is exhausted. Cached surfaces keep working."""

    code = "budget_exhausted"


class NotCachedError(Sept11Error):
    """The bytes are not in the local cache and this surface does not fetch."""

    code = "not_cached"


class PolicyError(Sept11Error):
    """Refused by a project rule: PII screen, unreviewed evidence, disabled live search."""

    code = "refused"


class IntegrityError(Sept11Error):
    """Stored evidence contradicts its manifest: hash, size, schema or coverage."""

    code = "integrity_failure"


class DeadlineError(Sept11Error):
    """The tool ran past its wall-clock budget. A socket timeout is not a job deadline."""

    code = "deadline_exceeded"
