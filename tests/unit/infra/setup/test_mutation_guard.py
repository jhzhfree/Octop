"""Actual process locks on temporary installation homes, never business data."""

import multiprocessing
from types import SimpleNamespace

import pytest

from octop.config import DatabaseConfig, load_config
from octop.infra.db.rebind import persist_database_config
from octop.infra.errors import OctopError
from octop.infra.setup.mutation_guard import setup_mutation_guard
from octop.infra.utils.paths import PathLayout


def server(home):
    paths = PathLayout(home)
    load_config(paths.config)  # Materialize defaults before capturing the startup snapshot.
    return SimpleNamespace(paths=paths, config=load_config(paths.config), services=None)


def hold(home, ready, release):
    with setup_mutation_guard(server(home)):
        ready.set()
        if not release.wait(15):
            raise RuntimeError("TEST_TIMEOUT")


def stale_worker(home, ready, release, result):
    loaded = server(home)
    ready.set()
    if not release.wait(15):
        raise RuntimeError("TEST_TIMEOUT")
    try:
        with setup_mutation_guard(loaded):
            result.put("entered")
    except OctopError as exc:
        result.put(str(exc))


@pytest.mark.parametrize("crash", [False, True])
def test_other_process_excluded_and_lock_released(tmp_path, crash):
    loaded = server(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    child = ctx.Process(target=hold, args=(tmp_path, ready, release))
    child.start()
    try:
        assert ready.wait(10)
        with (
            pytest.raises(OctopError, match="operation in progress"),
            setup_mutation_guard(loaded),
        ):
            pytest.fail("overlapping setup entered")
        if crash:
            child.terminate()
        else:
            release.set()
        child.join(10)
        assert not child.is_alive()
        with setup_mutation_guard(loaded):
            pass
    finally:
        if child.is_alive():
            child.terminate()
        child.join(5)


def test_stale_process_cannot_write_after_database_selection(tmp_path):
    loaded = server(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    ready, release, result = ctx.Event(), ctx.Event(), ctx.Queue()
    child = ctx.Process(target=stale_worker, args=(tmp_path, ready, release, result))
    child.start()
    try:
        assert ready.wait(10)
        with setup_mutation_guard(loaded):
            persist_database_config(loaded.paths.config, DatabaseConfig(sqlite_path="chosen.db"))
        release.set()
        assert "configuration changed" in result.get(timeout=10)
        child.join(10)
        assert child.exitcode == 0
        with setup_mutation_guard(server(tmp_path)):
            assert load_config(loaded.paths.config).database.sqlite_path == "chosen.db"
    finally:
        if child.is_alive():
            child.terminate()
        child.join(5)


def test_failed_mutation_releases_lock_without_changing_config(tmp_path):
    loaded = server(tmp_path)
    before = loaded.paths.config.read_bytes()
    with pytest.raises(OSError, match="injected"), setup_mutation_guard(loaded):
        raise OSError("injected")
    with setup_mutation_guard(loaded):
        assert loaded.paths.config.read_bytes() == before
