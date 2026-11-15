"""SQLite initialization claims retain their fail-closed boolean contract."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.setup_lifecycle import SetupLifecycleRepo


@pytest.fixture
def db(tmp_path: Path) -> Iterator[SqlitePool]:
    pool = SqlitePool(tmp_path / "setup.db")
    run_migrations(pool)
    try:
        yield pool
    finally:
        pool.close()


def test_transition_requires_current_phase_and_preserves_admin(db: SqlitePool) -> None:
    repo = SetupLifecycleRepo(db)
    assert repo.transition("active", "finishing", admin_id=99) is False
    assert repo.state() == ("pending", None)
    assert repo.transition("pending", "active", admin_id=7) is True
    assert repo.transition("pending", "active", admin_id=99) is False
    assert repo.state() == ("active", 7)
    assert repo.transition("active", "finishing") is True
    assert repo.state() == ("finishing", 7)
    assert repo.transition("active", "finishing") is False
    assert repo.transition("finishing", "completed") is True
    assert repo.transition("active", "finishing") is False
    assert repo.state() == ("completed", 7)


def test_rotation_only_in_active_phase_invalidates_previous_digest(db: SqlitePool) -> None:
    repo = SetupLifecycleRepo(db)
    assert repo.rotate_token("first") is False
    assert repo.token_matches("first") is False
    assert repo.transition("pending", "active", admin_id=7) is True
    assert repo.rotate_token("first") is True
    assert repo.token_matches("first") is True
    assert repo.rotate_token("second") is True
    assert repo.token_matches("first") is False
    assert repo.token_matches("second") is True
    for phase in ("finishing", "completed"):
        previous = repo.state()[0]
        assert repo.transition(previous, phase) is True
        assert repo.rotate_token("third") is False
        assert repo.token_matches("second") is False
        with db.connect() as conn:
            row = conn.execute("SELECT token_digest FROM setup_lifecycle WHERE id=1").fetchone()
        assert row["token_digest"] == "second"


def test_missing_lifecycle_row_never_claims_success(db: SqlitePool) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM setup_lifecycle")
    repo = SetupLifecycleRepo(db)
    assert repo.state() == ("completed", None)
    assert repo.transition("pending", "active", admin_id=7) is False
    assert repo.rotate_token("digest") is False
    assert repo.token_matches("digest") is False
    assert repo.state() == ("completed", None)
