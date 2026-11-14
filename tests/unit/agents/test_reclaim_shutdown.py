"""Deterministic coverage for reclaim worker shutdown and SQLite close ordering."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from octop.infra.agents.manager import AgentManager
from octop.infra.db.pool import SqlitePool


@pytest.mark.asyncio
async def test_shutdown_drains_sweep_thread_and_prevents_restart() -> None:
    manager = AgentManager.__new__(AgentManager)
    manager._reclaim_pending = set()
    manager._reclaim_task = None
    manager._reclaim_sweep_task = None
    manager._reclaim_stopping = False
    manager._harness_manager = None
    manager._lock = asyncio.Lock()
    manager.memory_slim = AsyncMock()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def scan() -> int:
        started.set()
        assert release.wait(5)
        manager._reclaim_pending.add("agent")
        finished.set()
        return 1

    manager._queue_bloated_sqlite_stores = scan  # type: ignore[method-assign]
    manager.schedule_idle_reclaim_sweep()
    first = manager._reclaim_sweep_task
    manager.schedule_idle_reclaim_sweep()
    assert manager._reclaim_sweep_task is first
    assert await asyncio.to_thread(started.wait, 5)
    stop = asyncio.create_task(manager.shutdown())
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not stop.done()
        assert not finished.is_set()
        manager.memory_slim.close.assert_not_awaited()
        manager.schedule_store_reclaim("late")
        assert "late" not in manager._reclaim_pending
    finally:
        release.set()
        await asyncio.wait_for(stop, 5)
    assert finished.is_set()
    assert manager._reclaim_task is None
    assert not manager._reclaim_pending
    manager.memory_slim.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_reclaim_waits_for_worker_even_on_second_cancel() -> None:
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def compact() -> bool:
        started.set()
        assert release.wait(5)
        finished.set()
        return True

    task = asyncio.create_task(AgentManager._await_reclaim_thread(compact))
    assert await asyncio.to_thread(started.wait, 5)
    try:
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert not finished.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    assert finished.is_set()


def test_pool_close_waits_for_connection_lock(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "test.db")
    attempted, closed = threading.Event(), threading.Event()

    def close() -> None:
        attempted.set()
        pool.close()
        closed.set()

    with pool.connect() as conn:
        worker = threading.Thread(target=close)
        worker.start()
        assert attempted.wait(5)
        assert not closed.wait(0.05)
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    worker.join(5)
    assert closed.is_set()


@pytest.mark.asyncio
async def test_failed_sweep_does_not_skip_active_reclaim_drain() -> None:
    manager = AgentManager.__new__(AgentManager)
    manager._reclaim_pending = set()
    manager._harness_manager = None
    manager._lock = asyncio.Lock()
    manager.memory_slim = AsyncMock()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    async def failed_sweep() -> None:
        raise RuntimeError("scan failed")

    def reclaim() -> None:
        started.set()
        assert release.wait(5)
        finished.set()

    manager._reclaim_sweep_task = asyncio.create_task(failed_sweep())
    manager._reclaim_task = asyncio.create_task(manager._await_reclaim_thread(reclaim))
    assert await asyncio.to_thread(started.wait, 5)
    assert manager._reclaim_sweep_task.done()
    stop = asyncio.create_task(manager.shutdown())
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not stop.done()
        manager.memory_slim.close.assert_not_awaited()
    finally:
        release.set()
        await asyncio.wait_for(stop, 5)
    assert finished.is_set()
    manager.memory_slim.close.assert_awaited_once()
