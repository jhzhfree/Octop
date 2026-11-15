"""Synthetic SQLite admission tests; no service or real identity access."""

import multiprocessing
from pathlib import Path

import pytest

from octop.infra.auth.renewal import require_renewal_budget
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.auth_renew_buckets import AuthRenewBucketRepo
from octop.infra.errors import OctopError


@pytest.fixture
def database(tmp_path):
    pool = SqlitePool(tmp_path / "budget.db")
    run_migrations(pool)
    with pool.transaction() as conn:
        for user_id in (1, 2):
            conn.execute(
                "INSERT INTO users(id,username,password_hash,role,created_at) VALUES(?,?,NULL,'user',1)",
                (user_id, f"synthetic-{user_id}"),
            )
    yield pool
    pool.close()


def test_budget_is_per_account_and_survives_reopen(database):
    repo = AuthRenewBucketRepo(database)
    assert [repo.admit(1, 100) for _ in range(10)] == [0] * 10
    other = SqlitePool(database.path)
    try:
        assert AuthRenewBucketRepo(other).admit(1, 100) == 1
        assert AuthRenewBucketRepo(other).admit(2, 100) == 0
        assert AuthRenewBucketRepo(other).admit(1, 99) == 2
        assert AuthRenewBucketRepo(other).admit(1, 101) == 0
    finally:
        other.close()
    with database.transaction() as conn:
        conn.execute("DELETE FROM users WHERE id=1")
    assert repo.admit(1, 102) is None
    with database.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM auth_renew_buckets").fetchone()[0] == 1


def _worker(path, event, results):
    pool = SqlitePool(Path(path))
    try:
        event.wait(10)
        results.put([AuthRenewBucketRepo(pool).admit(1, 100) for _ in range(8)])
    finally:
        pool.close()


def test_separate_processes_cannot_each_claim_a_full_budget(database):
    context = multiprocessing.get_context("spawn")
    event = context.Event()
    results = context.Queue()
    children = [
        context.Process(target=_worker, args=(str(database.path), event, results)) for _ in range(4)
    ]
    try:
        for child in children:
            child.start()
        event.set()
        admissions = [value for _ in children for value in results.get(timeout=30)]
        for child in children:
            child.join(timeout=10)
            assert child.exitcode == 0
        assert admissions.count(0) == 10
        assert admissions.count(1) == 22
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
        results.close()


def test_database_failure_cannot_fall_back_to_unlimited_renewal(database, monkeypatch):
    def unavailable(*args):
        raise RuntimeError("synthetic database failure")

    monkeypatch.setattr(AuthRenewBucketRepo, "admit", unavailable)
    with pytest.raises(OctopError) as error:
        require_renewal_budget(database, 1)
    assert error.value.status == 503


def test_migration_upgrades_existing_42_database_without_changing_users(tmp_path, monkeypatch):
    from octop.infra.db import migrate

    pool = SqlitePool(tmp_path / "upgrade.db")
    discover = migrate._discover
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                migrate,
                "_discover",
                lambda dialect="sqlite": [(v, p) for v, p in discover(dialect) if v <= 42],
            )
            run_migrations(pool)
        with pool.transaction() as conn:
            conn.execute(
                "INSERT INTO users(id,username,password_hash,role,created_at) VALUES(1,'existing',NULL,'user',1)"
            )
        run_migrations(pool)
        run_migrations(pool)
        assert AuthRenewBucketRepo(pool).admit(1, 100) == 0
        with pool.connect() as conn:
            assert conn.execute("SELECT username FROM users").fetchone()[0] == "existing"
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 43
    finally:
        pool.close()
