"""Atomic per-account renewal admission, shared by workers using the same DB."""

from octop.infra.db.pool import DatabasePool


class AuthRenewBucketRepo:
    def __init__(self, db: DatabasePool):
        self._db = db

    def admit(self, user_id: int, now: int) -> int | None:
        """Admit up to ten requests per UTC second; return retry seconds or zero.

        None means the persisted account is missing or disabled.
        Fixed-window boundaries can admit twenty requests around a second tick.
        Backward clock movement never resets an existing budget early.
        """
        with self._db.transaction() as conn:
            user = conn.execute("SELECT disabled FROM users WHERE id=?", (user_id,)).fetchone()
            if user is None or user["disabled"]:
                return None
            row = conn.execute(
                "INSERT INTO auth_renew_buckets(user_id,window_start,attempts) VALUES(?,?,1) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "window_start=CASE WHEN excluded.window_start>auth_renew_buckets.window_start "
                "THEN excluded.window_start ELSE auth_renew_buckets.window_start END, "
                "attempts=CASE WHEN excluded.window_start>auth_renew_buckets.window_start "
                "THEN 1 ELSE auth_renew_buckets.attempts+1 END "
                "WHERE excluded.window_start>auth_renew_buckets.window_start "
                "OR auth_renew_buckets.attempts<10 RETURNING window_start",
                (user_id, now),
            ).fetchone()
            if row is not None:
                return 0
            bucket = conn.execute(
                "SELECT window_start FROM auth_renew_buckets WHERE user_id=?", (user_id,)
            ).fetchone()
            if bucket is None:
                raise RuntimeError("renewal bucket unavailable")
            return max(1, int(bucket["window_start"]) + 1 - now)
