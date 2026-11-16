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


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_cross_home_shared_config_is_rejected(tmp_path, kind):
    import os

    first_home, second_home = tmp_path / "first", tmp_path / "second"
    first = server(first_home)
    second_home.mkdir()
    shared = second_home / "config.json"
    try:
        if kind == "symlink":
            shared.symlink_to(first.paths.config)
        else:
            os.link(first.paths.config, shared)
    except OSError:
        pytest.skip("filesystem link unavailable")
    second = SimpleNamespace(paths=PathLayout(second_home), config=first.config, services=None)
    before = first.paths.config.read_bytes()
    with pytest.raises(OctopError, match="private, unaliased"), setup_mutation_guard(second):
        pytest.fail("shared configuration accepted")
    assert first.paths.config.read_bytes() == before


def test_home_directory_alias_uses_same_lock(tmp_path):
    loaded = server(tmp_path / "real")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(loaded.paths.root, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    other = SimpleNamespace(paths=PathLayout(alias), config=loaded.config, services=None)
    with (
        setup_mutation_guard(loaded),
        pytest.raises(OctopError, match="operation in progress"),
        setup_mutation_guard(other),
    ):
        pytest.fail("directory alias bypassed lock")
