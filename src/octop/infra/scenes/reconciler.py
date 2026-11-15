"""Trusted maintenance pass for scene runs; no user request is required."""

from __future__ import annotations

import time
from typing import Any


def reconcile_queued_scene_runs(
    repos: Any, *, limit: int = 200, timeout_seconds: int = 3600
) -> dict[str, int]:
    """Synchronize terminal Cron results using compare-and-set DB updates.

    A missing job is recorded as failed. Unknown/in-progress Cron status is
    preserved as queued: a retry is never automatic because it could duplicate
    an external side effect.
    """
    if timeout_seconds < 60:
        raise ValueError("timeout_seconds must be at least 60")
    stats = {"scanned": 0, "succeeded": 0, "failed": 0, "pending": 0, "stalled": 0}
    from octop.infra.db.repos.scene_inspection import SceneInspectionRepo

    now = int(time.time())
    for run in repos.enterprise_scene_repo.list_queued_runs(limit=limit):
        stats["scanned"] += 1
        run_id = str(run["run_id"])
        cron_id = run.get("cron_id")
        if not cron_id:
            if (run.get("plan_snapshot") or {}).get("inspection"):
                SceneInspectionRepo(repos.db).fail(run_id)
            repos.enterprise_scene_repo.mark_queued_failed(
                run_id, cron_id=None, summary="Queued scene has no Cron identity"
            )
            stats["failed"] += 1
            continue
        cron = repos.cron_repo.get(str(cron_id))
        if cron is None:
            if (run.get("plan_snapshot") or {}).get("inspection"):
                SceneInspectionRepo(repos.db).fail(run_id)
            repos.enterprise_scene_repo.mark_queued_failed(
                run_id, cron_id=str(cron_id), summary="Cron job disappeared before completion"
            )
            stats["failed"] += 1
            continue
        if cron.last_status not in {"ok", "error"}:
            # Do not force-fail or replay a task that may still be executing.
            # Surface overdue runs for investigation without side effects.
            if now - int(run.get("updated_at") or now) >= timeout_seconds:
                if (run.get("plan_snapshot") or {}).get("inspection"):
                    # No replay: an interrupted read may have happened. Mark the
                    # abandoned batch failed; a fresh request needs new approval.
                    SceneInspectionRepo(repos.db).fail(run_id)
                    repos.enterprise_scene_repo.mark_queued_failed(
                        run_id,
                        cron_id=str(cron_id),
                        summary="Inspection execution interrupted or timed out",
                    )
                    stats["failed"] += 1
                else:
                    stats["stalled"] += 1
            else:
                stats["pending"] += 1
            continue
        if cron.last_status == "error" and (run.get("plan_snapshot") or {}).get("inspection"):
            SceneInspectionRepo(repos.db).fail(run_id)
        terminal = "succeeded" if cron.last_status == "ok" else "failed"
        repos.enterprise_scene_repo.sync_cron_status(run_id, cron_id=str(cron_id), status=terminal)
        stats[terminal] += 1
    return stats
