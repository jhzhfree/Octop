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

    def queue(
        self,
        organization_id: str,
        *,
        category: str,
        limit: int = 20,
        before: int | None = None,
        worker_id: str | None = None,
        goal_id: str | None = None,
    ) -> dict[str, Any]:
        if worker_id is not None and (
            not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 128
        ):
            raise ValueError("invalid worker filter")
        if goal_id is not None and (
            not isinstance(goal_id, str) or not goal_id.strip() or len(goal_id) > 128
        ):
            raise ValueError("invalid goal filter")
        if category not in {
            "awaiting_approval",
            "queued",
            "rejected",
            "failed",
            "unreviewed_success",
            "changes_requested",
            "accepted",
        }:
            raise ValueError("invalid queue category")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        if before is not None and (type(before) is not int or not 1 <= before <= 2**63 - 1):
            raise ValueError("invalid queue cursor")
        return {
            "organization_id": organization_id,
            "category": category,
            "worker_id": worker_id,
            "goal_id": goal_id,
            "as_of": int(time.time()),
            "execution_authorized": False,
            "persisted_state_may_lag": True,
            **self.repo.queue(
                organization_id,
                self.user_id,
                category=category,
                limit=limit,
                before=before,
                worker_id=worker_id,
                goal_id=goal_id,
            ),
        }

    def detail(self, organization_id: str, run_id: str) -> dict[str, Any] | None:
        row = self.repo.detail(organization_id, self.user_id, run_id)
        if row is None:
            return None
        return {**row, "as_of": int(time.time()), "persisted_state_may_lag": True}
