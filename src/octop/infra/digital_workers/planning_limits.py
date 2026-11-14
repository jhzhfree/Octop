"""Atomic process-local planning admission; not a distributed or monetary budget."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from octop.i18n import tr
from octop.infra.digital_workers.service import DigitalWorkerError


class PlanningLimitError(DigitalWorkerError):
    def __init__(self, reason: str, locale: str, retry_after: int) -> None:
        super().__init__(
            "DIGITAL_WORKER_PLANNING_" + reason.upper(),
            tr("planningDialogue." + reason, locale),
            429,
        )
        self.retry_after = retry_after


class PlanningLimiter:
    """One active call per user, four globally; rolling 6/60 calls per minute.

    All users and organizations in this process share the global limits. No
    await occurs while holding the lock. Admission is counted even on errors.
    Restarting or adding a process resets/adds capacity; enforce monetary and
    cluster-wide quotas in the model gateway separately.
    """

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._lock = threading.Lock()
        self._admissions: deque[tuple[float, int]] = deque()
        self._active: set[int] = set()

    @contextmanager
    def admit(self, user_id: int, locale: str) -> Iterator[None]:
        with self._lock:
            now = self._now()
            while self._admissions and self._admissions[0][0] <= now - 60:
                self._admissions.popleft()
            user_calls = [at for at, actor in self._admissions if actor == user_id]
            if len(user_calls) >= 6 or len(self._admissions) >= 60:
                deadlines = []
                if len(user_calls) >= 6:
                    deadlines.append(user_calls[0] + 60)
                if len(self._admissions) >= 60:
                    deadlines.append(self._admissions[0][0] + 60)
                raise PlanningLimitError(
                    "rate_limited", locale, max(1, math.ceil(max(deadlines) - now))
                )
            if user_id in self._active or len(self._active) >= 4:
                raise PlanningLimitError("busy", locale, 5)
            self._active.add(user_id)
            self._admissions.append((now, user_id))
        try:
            yield
        finally:
            with self._lock:
                self._active.remove(user_id)


PLANNING_LIMITER = PlanningLimiter()
