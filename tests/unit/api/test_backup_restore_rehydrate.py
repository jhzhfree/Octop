"""Online restore rejection and unaffected backup creation/status paths."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from octop.api.routers import backup as backup_router
from octop.infra.backup.auto import BACKUP_LOCK
from octop.infra.backup.store import BackupFileInfo
from octop.infra.errors import ErrorCode, OctopError


@pytest.mark.asyncio
@pytest.mark.parametrize("restore_config", [False, True])
@pytest.mark.parametrize("driver", ["sqlite", "postgresql"])
async def test_online_restore_rejects_before_any_io(monkeypatch, restore_config, driver):
    server = MagicMock()
    server.services.db.dialect = driver
    thread = MagicMock(side_effect=AssertionError("must not dispatch restore work"))
    monkeypatch.setattr(backup_router.asyncio, "to_thread", thread)
    with pytest.raises(OctopError) as error:
        await backup_router.restore_backup_file(
            filename="old-migration-backup.tar.gz",
            restore_config=restore_config,
            user=MagicMock(id=1, username="admin"),
            server=server,
        )
    assert error.value.code == ErrorCode.BACKUP_ONLINE_RESTORE_UNAVAILABLE
    assert error.value.status == 409
    thread.assert_not_called()
    server.services.db.connect.assert_not_called()
    server.services.audit_repo.write.assert_not_called()
    server.app_runtime.agent_registry.on_provider_changed.assert_not_called()
    server.app_runtime.gateway.reload_channels_from_db.assert_not_called()
    server.app_runtime.cron_manager.reload_from_db.assert_not_called()


@pytest.mark.asyncio
async def test_create_backup_offloads_to_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Manual create must not run tar/sqlite work on the event loop."""
    entry = BackupFileInfo(
        name="octop-backup-20260101T000000Z.tar.gz",
        size=12,
        modified_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
    )
    called: dict[str, Any] = {}

    def _fake_create(**kwargs: Any) -> BackupFileInfo:
        called.update(kwargs)
        return entry

    monkeypatch.setattr(backup_router, "_create_and_store_manual_backup", _fake_create)
    monkeypatch.setattr(backup_router, "_agent_rows", lambda _server: [])

    server = MagicMock()
    server.services = MagicMock()
    server.services.db = object()
    server.services.config.database = object()
    server.paths = object()
    server.app_runtime = MagicMock()

    result = await backup_router.create_backup(_=None, server=server)

    assert result == {"ok": True, "item": entry.to_dict()}
    assert called["pool"] is server.services.db
    assert called["options"].include_chats is False


@pytest.mark.asyncio
async def test_create_backup_passes_selected_content(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = BackupFileInfo(
        name="octop-backup-20260101T000000Z.tar.gz",
        size=12,
        modified_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
    )
    called: dict[str, Any] = {}

    def _fake_create(**kwargs: Any) -> BackupFileInfo:
        called.update(kwargs)
        return entry

    monkeypatch.setattr(backup_router, "_create_and_store_manual_backup", _fake_create)
    monkeypatch.setattr(backup_router, "_agent_rows", lambda _server: [])
    server = MagicMock()
    server.services.db = object()
    server.services.config.database = object()

    body = backup_router.CreateBackupBody(
        include_config=False,
        include_workspaces=False,
        include_chats=True,
    )
    await backup_router.create_backup(body=body, _=None, server=server)

    assert called["options"] == body


@pytest.mark.asyncio
async def test_create_backup_rejects_when_lock_held() -> None:
    await BACKUP_LOCK.acquire()
    try:
        with pytest.raises(OctopError) as exc_info:
            await backup_router.create_backup(_=None, server=MagicMock(services=MagicMock()))
        assert exc_info.value.code == ErrorCode.BACKUP_IN_PROGRESS
    finally:
        BACKUP_LOCK.release()


@pytest.mark.asyncio
async def test_restore_backup_rejects_when_lock_held(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup_router, "normalize_backup_filename", lambda name: name)
    await BACKUP_LOCK.acquire()
    try:
        with pytest.raises(OctopError) as exc_info:
            await backup_router.restore_backup_file(
                filename="octop-backup.tar.gz",
                restore_config=True,
                user=MagicMock(id=1, username="admin"),
                server=MagicMock(services=MagicMock()),
            )
        assert exc_info.value.code == ErrorCode.BACKUP_ONLINE_RESTORE_UNAVAILABLE
    finally:
        BACKUP_LOCK.release()


@pytest.mark.asyncio
async def test_backup_status_reports_idle_and_busy() -> None:
    from octop.infra.backup.auto import hold_backup_lock

    idle = await backup_router.get_backup_status(_=None)
    assert idle == {"busy": False, "operation": None}

    async with hold_backup_lock("create"):
        busy = await backup_router.get_backup_status(_=None)
        assert busy == {"busy": True, "operation": "create"}
