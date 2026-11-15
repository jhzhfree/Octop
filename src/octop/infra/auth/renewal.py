"""Admission control for authenticated explicit renewal, not proof of activity."""

import time

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos.auth_renew_buckets import AuthRenewBucketRepo
from octop.infra.errors import ErrorCode, OctopError


class RenewalThrottled(OctopError):
    def __init__(self, retry_after: int):
        error = OctopError.localized(
            ErrorCode.RATE_LIMITED, details={"retry_after_seconds": retry_after}
        )
        super().__init__(error.code, error.message, error.status, error.details)
        self.retry_after = retry_after


def require_renewal_budget(db: DatabasePool, user_id: int) -> None:
    try:
        retry_after = AuthRenewBucketRepo(db).admit(user_id, int(time.time()))
    except Exception as exc:
        raise OctopError.localized(ErrorCode.INTERNAL_ERROR, status=503) from exc
    if retry_after is None:
        raise OctopError.localized(ErrorCode.USER_DISABLED)
    if retry_after:
        raise RenewalThrottled(retry_after)
