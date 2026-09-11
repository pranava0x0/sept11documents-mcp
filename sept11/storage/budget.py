"""Rate and byte budgets shared across processes (spec 08: limits apply across processes).

Two independent processes politely spacing their own requests one second apart
still hit the City twice a second. The state file below is the shared clock and
the shared daily byte counter; retries spend from the same budget.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from ..core.errors import BudgetError

try:  # POSIX only; without it the budget degrades to per-process.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        raw = handle.read()
        try:
            state = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            state = {}
        yield state
        handle.seek(0)
        handle.truncate()
        json.dump(state, handle)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class Budget:
    """Shared politeness clock and daily byte ceiling."""

    def __init__(self, state_path: Path, daily_bytes: int):
        self.state_path = Path(state_path)
        self.daily_bytes = daily_bytes
        self._local: dict[str, float] = {}

    # -- rate ---------------------------------------------------------------
    def reserve_slot(self, host: str, min_interval: float) -> float:
        """Claim the next slot for `host`; returns the seconds the caller must sleep."""
        try:
            with _locked(self.state_path) as state:
                hosts = state.setdefault("hosts", {})
                now = time.time()
                earliest = max(now, float(hosts.get(host, 0.0)) + min_interval)
                hosts[host] = earliest
                return max(0.0, earliest - now)
        except OSError:  # unwritable state dir: fall back to this process only
            now = time.time()
            earliest = max(now, self._local.get(host, 0.0) + min_interval)
            self._local[host] = earliest
            return max(0.0, earliest - now)

    def wait_turn(self, host: str, min_interval: float, sleep=time.sleep) -> float:
        delay = self.reserve_slot(host, min_interval)
        if delay > 0:
            sleep(delay)
        return delay

    # -- bytes --------------------------------------------------------------
    def spend(self, count: int, today: str | None = None) -> int:
        """Charge `count` bytes against today's budget. Raises BudgetError when exhausted."""
        today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
        try:
            with _locked(self.state_path) as state:
                if state.get("day") != today:
                    state["day"], state["bytes"] = today, 0
                spent = int(state.get("bytes", 0)) + int(count)
                if spent > self.daily_bytes:
                    raise BudgetError(
                        f"daily byte budget exhausted ({state.get('bytes', 0)} of {self.daily_bytes} B used); "
                        "cached surfaces still work — raise SEPT11_BYTE_BUDGET_MB to fetch more today")
                state["bytes"] = spent
                return spent
        except OSError:
            return int(count)

    def spent_today(self, today: str | None = None) -> int:
        today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
        try:
            with _locked(self.state_path) as state:
                return int(state.get("bytes", 0)) if state.get("day") == today else 0
        except OSError:
            return 0
