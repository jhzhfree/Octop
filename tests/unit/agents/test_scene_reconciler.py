"""Reconciler behavior without live services."""

from types import SimpleNamespace
from unittest.mock import Mock

from octop.infra.scenes.reconciler import reconcile_queued_scene_runs


def test_reconcile_missing_job_fails_without_retry():
    repo = Mock()
    repo.enterprise_scene_repo.list_queued_runs.return_value = [
        {"run_id": "run-1", "cron_id": "cron-1"}
    ]
    repo.cron_repo.get.return_value = None
    assert reconcile_queued_scene_runs(repo)["failed"] == 1
    repo.enterprise_scene_repo.mark_queued_failed.assert_called_once_with(
        "run-1", cron_id="cron-1", summary="Cron job disappeared before completion"
    )
    repo.enterprise_scene_repo.sync_cron_status.assert_not_called()


def test_reconcile_in_progress_does_not_mark_terminal():
    repo = Mock()
    repo.enterprise_scene_repo.list_queued_runs.return_value = [
        {"run_id": "run-1", "cron_id": "cron-1"}
    ]
    repo.cron_repo.get.return_value = SimpleNamespace(last_status=None)
    assert reconcile_queued_scene_runs(repo)["pending"] == 1
    repo.enterprise_scene_repo.sync_cron_status.assert_not_called()


def test_reconcile_completed_job():
    repo = Mock()
    repo.enterprise_scene_repo.list_queued_runs.return_value = [
        {"run_id": "run-1", "cron_id": "cron-1"}
    ]
    repo.cron_repo.get.return_value = SimpleNamespace(last_status="ok")
    assert reconcile_queued_scene_runs(repo)["succeeded"] == 1
    repo.enterprise_scene_repo.sync_cron_status.assert_called_once_with(
        "run-1", cron_id="cron-1", status="succeeded"
    )
