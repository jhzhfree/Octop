"""Durable session revocation tombstones shared by all server workers."""

from octop.infra.db.pool import DatabasePool


class AuthSessionRevocationRepo:
    def __init__(self, db: DatabasePool):
        self._db = db

    def is_revoked(self, digest: str) -> bool:
        with self._db.connect() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM auth_session_revocations WHERE session_digest=?", (digest,)
                ).fetchone()
                is not None
            )

    def revoke(self, digest: str, now: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO auth_session_revocations(session_digest, revoked_at) VALUES (?, ?) "
                "ON CONFLICT(session_digest) DO NOTHING",
                (digest, now),
            )
