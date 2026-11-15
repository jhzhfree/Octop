"""Durable first-install gate; compare-and-set claims never span awaits."""

from __future__ import annotations

from octop.infra.db.pool import DatabasePool


class SetupLifecycleRepo:
    def __init__(self, db: DatabasePool):
        self._db = db

    def state(self) -> tuple[str, int | None]:
        with self._db.connect() as conn:
            row = conn.execute("SELECT phase, admin_id FROM setup_lifecycle WHERE id=1").fetchone()
        # Missing state is not permission to reopen an installation.
        return (row["phase"], row["admin_id"]) if row else ("completed", None)

    def transition(self, expected: str, target: str, *, admin_id: int | None = None) -> bool:
        with self._db.transaction() as conn:
            result = conn.execute(
                "UPDATE setup_lifecycle SET phase=?, admin_id=COALESCE(?, admin_id) "
                "WHERE id=1 AND phase=?",
                (target, admin_id, expected),
            )
            return result.rowcount == 1

    def token_matches(self, digest: str) -> bool:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM setup_lifecycle WHERE id=1 AND phase='active' AND token_digest=?",
                (digest,),
            ).fetchone()
        return row is not None

    def rotate_token(self, digest: str) -> bool:
        with self._db.transaction() as conn:
            result = conn.execute(
                "UPDATE setup_lifecycle SET token_digest=? WHERE id=1 AND phase='active'",
                (digest,),
            )
            return result.rowcount == 1
