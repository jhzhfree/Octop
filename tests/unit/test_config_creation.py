"""First-creation races use real child processes and temporary configs only."""

import json
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.config import DatabaseConfig, load_config
from octop.infra.db.rebind import persist_database_config
from octop.infra.setup.mutation_guard import setup_mutation_guard
from octop.infra.utils.paths import PathLayout


def delayed_defaults(path, ready, release, result):
    # Exercise the historical unsafe publication and the new atomic publication
    # at the same boundary: defaults prepared but destination not yet written.
    original_write, original_link = Path.write_text, os.link

    def pause():
        ready.set()
        if not release.wait(15):
            raise RuntimeError("TEST_RELEASE_TIMEOUT")

    def write(self, *args, **kwargs):
        if self == path:
            pause()
        return original_write(self, *args, **kwargs)

    def link(src, dst, *args, **kwargs):
        if Path(dst) == path:
            pause()
        return original_link(src, dst, *args, **kwargs)

    Path.write_text, os.link = write, link
    try:
        cfg = load_config(path)
        result.put((cfg.database.sqlite_path, cfg.database_in_file))
    finally:
        Path.write_text, os.link = original_write, original_link


def test_delayed_first_creator_rereads_winner_under_setup_guard(tmp_path):
    path = tmp_path / "config.json"
    ctx = multiprocessing.get_context("spawn")
    ready, release, result = ctx.Event(), ctx.Event(), ctx.Queue()
    child = ctx.Process(target=delayed_defaults, args=(path, ready, release, result))
    child.start()
    try:
        assert ready.wait(10)
        load_config(path)
        loaded = SimpleNamespace(
            paths=PathLayout(tmp_path), config=load_config(path), services=None
        )
        with setup_mutation_guard(loaded):
            persist_database_config(path, DatabaseConfig(sqlite_path="chosen.db"))
            before = path.read_bytes()
            release.set()
            assert result.get(timeout=10) == ("chosen.db", True)
            assert path.read_bytes() == before
        child.join(10)
        assert child.exitcode == 0
        assert load_config(path).database.sqlite_path == "chosen.db"
    finally:
        if child.is_alive():
            child.terminate()
        child.join(5)


def test_two_first_creators_publish_complete_json(tmp_path):
    path = tmp_path / "config.json"
    ctx = multiprocessing.get_context("spawn")
    release, result = ctx.Event(), ctx.Queue()
    ready = [ctx.Event(), ctx.Event()]
    children = [
        ctx.Process(target=delayed_defaults, args=(path, event, release, result)) for event in ready
    ]
    for child in children:
        child.start()
    try:
        assert all(event.wait(10) for event in ready)
        assert not path.exists()
        release.set()
        outcomes = [result.get(timeout=10), result.get(timeout=10)]
        assert sorted(flag for _, flag in outcomes) == [False, True]
        assert json.loads(path.read_text())["database"]["sqlite_path"] == "octop.db"
        for child in children:
            child.join(10)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(5)


@pytest.mark.parametrize("operation", ["fsync", "link"])
def test_publication_failure_leaves_no_partial_config(tmp_path, monkeypatch, operation):
    def fail(*args, **kwargs):
        raise OSError("injected publication failure")

    path = tmp_path / "config.json"
    with monkeypatch.context() as patch:
        patch.setattr(os, operation, fail)
        with pytest.raises(OSError, match="injected publication failure"):
            load_config(path)
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []
    assert load_config(path).database.sqlite_path == "octop.db"


def test_terminated_creator_never_publishes_partial_config(tmp_path):
    path = tmp_path / "config.json"
    ctx = multiprocessing.get_context("spawn")
    ready, release, result = ctx.Event(), ctx.Event(), ctx.Queue()
    child = ctx.Process(target=delayed_defaults, args=(path, ready, release, result))
    child.start()
    try:
        assert ready.wait(10)
        assert not path.exists()
        child.terminate()
        child.join(5)
        assert not child.is_alive()
        assert not path.exists()
        assert load_config(path).database.sqlite_path == "octop.db"
        assert json.loads(path.read_text())["database"]["sqlite_path"] == "octop.db"
    finally:
        if child.is_alive():
            child.terminate()
        child.join(5)


def test_config_file_symlink_is_rejected_without_modifying_target(tmp_path):
    target = tmp_path / "shared.json"
    target.write_text('{"database":{"driver":"sqlite","sqlite_path":"chosen.db"}}')
    alias = tmp_path / "other" / "config.json"
    alias.parent.mkdir()
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("file symlinks unavailable")
    before = target.read_bytes()
    with pytest.raises(ValueError, match="file symlinks are unsupported"):
        load_config(alias)
    assert target.read_bytes() == before
