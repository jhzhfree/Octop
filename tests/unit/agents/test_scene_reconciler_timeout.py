"""Timeout classification must never retry or fail an in-flight execution."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from octop.infra.scenes.reconciler import reconcile_queued_scene_runs


def test_overdue_scene_is_reported_not_reexecuted():
    repos = Mock()
    repos.enterprise_scene_repo.list_queued_runs.return_value = [
        {"run_id": "run-1", "cron_id": "cron-1", "updated_at": 100}
    ]
    repos.cron_repo.get.return_value = SimpleNamespace(last_status=None)
    with patch("octop.infra.scenes.reconciler.time.time", return_value=4000):
        stats = reconcile_queued_scene_runs(repos, timeout_seconds=3600)
    assert stats["stalled"] == 1
    repos.enterprise_scene_repo.mark_queued_failed.assert_not_called()
    repos.enterprise_scene_repo.sync_cron_status.assert_not_called()


def test_invalid_timeout_rejected():
    with pytest.raises(ValueError):
        reconcile_queued_scene_runs(Mock(), timeout_seconds=1)
