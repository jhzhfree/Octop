"""Organization run progress is not target completion or verified delivery evidence."""

import time
from typing import Any

from octop.infra.db.repos.organization_progress import OrganizationProgressRepo


class OrganizationProgressService:
    MAX_WINDOW_SECONDS = 90 * 86400

    def __init__(self, repo: OrganizationProgressRepo, user_id: int) -> None:
        self.repo = repo
        self.user_id = user_id

    def inspect(self, organization_id: str, *, start: int, end: int) -> dict[str, Any]:
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end > 253402300799
            or end <= start
            or end - start > self.MAX_WINDOW_SECONDS
        ):
            raise ValueError("require a positive creation-time window of at most 90 days")
        return {
            "organization_id": organization_id,
            "window_start": start,
            "window_end_exclusive": end,
            "observed_at": int(time.time()),
            "scope": "persisted_runs_by_creation_time",
            "unit": "run",
            "max_runs": self.repo.MAX_RUNS,
            "execution_authorized": False,
            "external_systems_verified": False,
            **self.repo.summarize(organization_id, self.user_id, start, end),
        }
