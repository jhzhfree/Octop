"""Online recovery gate: synthetic archives and ASGI, never a real restore."""

import hashlib

import pytest

from octop.infra.backup.system_archive import create_system_backup
from tests.support.auth import bearer, bootstrap_admin, create_user, login


@pytest.mark.parametrize("restore_config", ["true", "false"])
async def test_old_restore_requests_cannot_modify_state_or_start_runtime(
    patched_app_client, restore_config, monkeypatch
):
    client, server, home = patched_app_client
    await bootstrap_admin(client, home)
    token = await login(client)
    archive = server.paths.ensure_backups_dir() / "synthetic.tar.gz"
    create_system_backup(
        paths=server.paths,
        agent_rows=[],
        pool=server.services.db,
        db_config=server.services.config.database,
        dest=archive,
        include_config=False,
        include_workspaces=False,
        include_skill_packages=False,
        include_plugins=False,
        include_knowledge=False,
    )
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    sentinel = home / "recovery-sentinel"
    sentinel.write_text("current workspace content")
    with server.services.db.connect() as conn:
        before = hashlib.sha256(conn.serialize()).hexdigest()
    # If the old implementation is ever reintroduced, fail before touching data.
    from octop.infra.backup import system_archive

    def forbidden_restore(*args, **kwargs):
        raise AssertionError("online restore must not run")

    monkeypatch.setattr(system_archive, "restore_system_backup", forbidden_restore)
    response = await client.post(
        "/api/admin/backup/files/synthetic.tar.gz/restore",
        params={"restore_config": restore_config, "preserve_users": "true", "maintenance": "true"},
        headers=bearer(token),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BACKUP_ONLINE_RESTORE_UNAVAILABLE"
    with server.services.db.connect() as conn:
        assert hashlib.sha256(conn.serialize()).hexdigest() == before
    assert archive_hash == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert sentinel.read_text() == "current workspace content"
    # The authenticated session and archive retrieval remain usable.
    assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 200
    downloaded = await client.get("/api/admin/backup/files/synthetic.tar.gz", headers=bearer(token))
    assert downloaded.status_code == 200
    assert hashlib.sha256(downloaded.content).hexdigest() == archive_hash


async def test_online_restore_still_requires_auth_and_backup_permission(patched_app_client):
    client, _, home = patched_app_client
    await bootstrap_admin(client, home)
    admin = await login(client)
    await create_user(client, bearer(admin), username="limited", role="user")
    user = await login(client, username="limited")
    path = "/api/admin/backup/files/not-read.tar.gz/restore"
    assert (await client.post(path)).status_code == 401
    assert (await client.post(path, headers=bearer(user))).status_code == 403
    assert (await client.post(path, headers=bearer(admin))).status_code == 409
