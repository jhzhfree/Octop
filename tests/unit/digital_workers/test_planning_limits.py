"""Deterministic process-local admission tests: no external services."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Barrier

import pytest

from octop.infra.digital_workers.planning_limits import PlanningLimiter, PlanningLimitError


def test_user_limit_window_and_bounded_expiry():
    clock = [0.0]
    limiter = PlanningLimiter(lambda: clock[0])
    for _ in range(6):
        with limiter.admit(1, "en"):
            pass
    with pytest.raises(PlanningLimitError) as error, limiter.admit(1, "en"):
        pytest.fail("must not admit")
    assert error.value.retry_after == 60
    clock[0] = 60
    with limiter.admit(1, "en"):
        pass
    assert len(limiter._admissions) == 1


def test_global_capacity_and_rejected_admission_does_not_count():
    limiter = PlanningLimiter()
    with ExitStack() as stack:
        for user in range(4):
            stack.enter_context(limiter.admit(user, "en"))
        for user in (0, 4):
            with pytest.raises(PlanningLimitError) as error, limiter.admit(user, "en"):
                pytest.fail("must not admit")
            assert error.value.code == "DIGITAL_WORKER_PLANNING_BUSY"
        assert len(limiter._admissions) == 4
    with limiter.admit(4, "en"):
        pass
    assert not limiter._active


def test_global_rate_limit_across_distinct_users():
    limiter = PlanningLimiter(lambda: 0.0)
    for user in range(60):
        with limiter.admit(user, "en"):
            pass
    with pytest.raises(PlanningLimitError) as error, limiter.admit(60, "zh"):
        pytest.fail("must not admit")
    assert error.value.retry_after == 60
    assert len(limiter._admissions) == 60


def test_exception_releases_slot_but_preserves_attempt():
    limiter = PlanningLimiter()
    with pytest.raises(RuntimeError), limiter.admit(1, "en"):
        raise RuntimeError("failed")
    with limiter.admit(1, "en"):
        pass
    assert len(limiter._admissions) == 2
    assert not limiter._active


def test_concurrent_admission_is_atomic_across_threads():
    limiter = PlanningLimiter()
    ready = Barrier(8)
    admitted = Barrier(8)

    def attempt(user):
        ready.wait(timeout=10)
        context = limiter.admit(user, "en")
        try:
            context.__enter__()
        except PlanningLimitError:
            admitted.wait(timeout=10)
            return False
        else:
            try:
                admitted.wait(timeout=10)
            finally:
                context.__exit__(None, None, None)
            return True

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(8)))
    assert sum(results) == 4
    assert not limiter._active


async def test_async_cancellation_releases_slot_but_preserves_rate_attempt():
    limiter = PlanningLimiter()
    entered = asyncio.Event()

    async def work():
        with limiter.admit(1, "en"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with limiter.admit(1, "en"):
        pass
    assert not limiter._active
    assert len(limiter._admissions) == 2
