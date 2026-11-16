"""Cooperating startup processes serialize migrations on a temporary SQLite DB."""

import multiprocessing
import os
from pathlib import Path

import pytest

from octop.infra.db import migrate
from octop.infra.db.pool import SqlitePool


def startup(path, started, entered, release, results, serialized, pause_in_transaction):
    db = SqlitePool(Path(path))
    apply = migrate._apply_sqlite_migration

    def hold(pool, version, script):
        if version == 35:

            def pause():
                entered.set()
                if not release.wait(15):
                    raise RuntimeError("TEST_RELEASE_TIMEOUT")
                return 1

            if pause_in_transaction:
                with pool.connect() as conn:
                    conn.create_function("pause_upgrade", 0, pause)
                    conn.execute(
                        "CREATE TEMP TRIGGER pause_upgrade_step BEFORE UPDATE ON _schema_version WHEN NEW.version=35 BEGIN SELECT pause_upgrade(); END"
                    )
            else:
                pause()
        return apply(pool, version, script)

    migrate._apply_sqlite_migration = hold
    started.set()
    try:
        (migrate.run_migrations if serialized else migrate._run_migrations)(db)
        results.put("ok")
    except Exception as exc:
        results.put(str(exc))
    finally:
        db.close()


@pytest.fixture
def upgrade_path(tmp_path, monkeypatch):
    path = tmp_path / "upgrade.db"
    db = SqlitePool(path)
    discover = migrate._discover
    with monkeypatch.context() as patch:
        patch.setattr(
            migrate,
            "_discover",
            lambda dialect: [
                (version, script) for version, script in discover(dialect) if version <= 34
            ],
        )
        migrate.run_migrations(db)
    db.close()
    return path


def process(ctx, path, *, serialized=True, pause_in_transaction=False):
    started, entered, release, results = ctx.Event(), ctx.Event(), ctx.Event(), ctx.Queue()
    child = ctx.Process(
        target=startup,
        args=(str(path), started, entered, release, results, serialized, pause_in_transaction),
    )
    return child, started, entered, release, results


def stop(child, release):
    if not child.is_alive():
        child.join(1)
        return
    release.set()
    child.join(10)
    if child.is_alive():
        child.terminate()
        child.join(5)


def test_two_startups_serialize_and_reread_version(upgrade_path):
    ctx = multiprocessing.get_context("spawn")
    first, started1, entered1, release1, results1 = process(ctx, upgrade_path)
    second, started2, entered2, release2, results2 = process(ctx, upgrade_path)
    first.start()
    try:
        assert entered1.wait(10)
        second.start()
        try:
            assert started2.wait(10)
            assert not entered2.wait(0.5), "second process entered the same migration"
            release1.set()
            release2.set()
            assert results1.get(timeout=10) == "ok"
            assert results2.get(timeout=10) == "ok"
            assert not entered2.is_set(), "waiting process did not reread the watermark"
        finally:
            stop(second, release2)
    finally:
        stop(first, release1)
    db = SqlitePool(upgrade_path)
    try:
        with db.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[
                0
            ] == migrate.schema_baseline_version("sqlite")
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        db.close()


def test_unserialized_stale_decision_reproduces_duplicate_migration(upgrade_path):
    # The internal body is the pre-mutex runner. Force both processes to decide
    # to apply 035 before either commits, rather than relying on scheduling luck.
    ctx = multiprocessing.get_context("spawn")
    first, started1, entered1, release1, results1 = process(ctx, upgrade_path, serialized=False)
    second, started2, entered2, release2, results2 = process(ctx, upgrade_path, serialized=False)
    first.start()
    second.start()
    try:
        assert entered1.wait(10) and entered2.wait(10)
        release1.set()
        assert results1.get(timeout=10) == "ok"
        release2.set()
        assert "duplicate column name" in results2.get(timeout=10)
    finally:
        stop(first, release1)
        stop(second, release2)


@pytest.mark.parametrize("pause_in_transaction", [False, True])
def test_process_termination_releases_migration_mutex(upgrade_path, pause_in_transaction):
    ctx = multiprocessing.get_context("spawn")
    holder, started, entered, release, results = process(
        ctx, upgrade_path, pause_in_transaction=pause_in_transaction
    )
    holder.start()
    try:
        assert entered.wait(10)
        holder.terminate()
        holder.join(5)
        assert not holder.is_alive()
    finally:
        stop(holder, release)
    # No lock-file deletion, lease expiry or administrative unlock is needed.
    db = SqlitePool(upgrade_path)
    try:
        migrate.run_migrations(db)
        with db.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[
                0
            ] == migrate.schema_baseline_version("sqlite")
    finally:
        db.close()


def test_wait_timeout_fails_closed_then_allows_retry(upgrade_path):
    ctx = multiprocessing.get_context("spawn")
    holder, started, entered, release, results = process(ctx, upgrade_path)
    holder.start()
    db = None
    try:
        assert entered.wait(10)
        db = SqlitePool(upgrade_path)
        with pytest.raises(RuntimeError, match="DATABASE_MIGRATION_BUSY"):
            migrate.run_migrations(db)
        with db.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 34
    finally:
        stop(holder, release)
    try:
        migrate.run_migrations(db)
    finally:
        db.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink fixture")
def test_companion_symlink_is_rejected_before_migration(upgrade_path):
    mutex = upgrade_path.with_name(upgrade_path.name + ".migration-lock")
    target = upgrade_path.with_name("unrelated-lock")
    target.write_bytes(b"")
    mutex.unlink()
    mutex.symlink_to(target)
    db = SqlitePool(upgrade_path)
    try:
        with pytest.raises(RuntimeError, match="DATABASE_MIGRATION_LOCK_UNSAFE"):
            migrate.run_migrations(db)
        with db.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 34
        assert target.read_bytes() == b""
    finally:
        db.close()
