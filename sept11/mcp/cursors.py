"""Opaque, bound cursors (spec 08: "Bind opaque cursors to query hash and snapshot").

A cursor is only valid for the query and the publication version that produced
it. Replaying one against a different query would silently return the wrong
page of the wrong result set, so a mismatch is rejected rather than repaired.
Cursors are signed with a key generated per process: a restart invalidates
outstanding cursors, which is the safe direction.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from ..config import CURSOR_TTL_SECONDS
from ..core.errors import InputError

_KEY = os.urandom(32)


def _fingerprint(query: str, snapshot: str | None) -> str:
    return hashlib.sha256(f"{query}\x00{snapshot or ''}".encode("utf-8")).hexdigest()[:16]


def issue(query: str, snapshot: str | None, offset: int) -> str:
    body = json.dumps({"f": _fingerprint(query, snapshot), "o": offset, "t": int(time.time())},
                      sort_keys=True).encode("utf-8")
    # The signature travels as hex: a raw digest can contain a "." byte, and splitting on
    # the last "." then rejected about one cursor in sixteen as "not issued by this server".
    signature = hmac.new(_KEY, body, hashlib.sha256).digest()[:16].hex().encode("ascii")
    return base64.urlsafe_b64encode(body + b"." + signature).decode("ascii")


def parse(cursor: str, query: str, snapshot: str | None, now: float | None = None) -> int:
    """Return the offset a cursor points at, or raise InputError explaining the refusal."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        body, signature = raw.rsplit(b".", 1)
        payload = json.loads(body)
    except Exception as exc:
        raise InputError("cursor is not a cursor issued by this server") from exc
    expected = hmac.new(_KEY, body, hashlib.sha256).digest()[:16].hex().encode("ascii")
    if not hmac.compare_digest(expected, signature):
        raise InputError("cursor signature does not verify; it was not issued by this process")
    if payload.get("f") != _fingerprint(query, snapshot):
        raise InputError(
            "cursor was issued for a different query or a different catalog snapshot; "
            "start again from the first page so the results stay comparable")
    age = (now if now is not None else time.time()) - float(payload.get("t", 0))
    if age > CURSOR_TTL_SECONDS:
        raise InputError(f"cursor expired after {CURSOR_TTL_SECONDS:.0f}s; start again from the first page")
    offset = payload.get("o")
    if not isinstance(offset, int) or offset < 0:
        raise InputError("cursor offset is invalid")
    return offset
