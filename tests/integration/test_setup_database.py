"""Setup wizard database step API tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.config import DatabaseConfig, OctopConfig, load_config
from octop.infra.db.factory import open_database
from octop.infra.db.migrate import run_migrations
from octop.infra.db.repos.users import UserRepo
from octop.infra.setup.password_file import read_password
from octop.infra.utils.paths import PathLayout
from tests.support.app import octop_client
from tests.support.auth import bootstrap_admin, wizard_token


def _seed_occupied_sqlite(home, *, relative_path: str = "occupied.db") -> None:
    """Create a sibling SQLite control-plane DB that already has one user."""
    paths = PathLayout(home)
    cfg = OctopConfig(
        database=DatabaseConfig(driver="sqlite", sqlite_path=relative_path),
        database_in_file=True,
    )
    db = open_database(cfg, paths)
    try:
        run_migrations(db)
        UserRepo(db).create(username="existing", password_hash="x", role="admin")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_setup_database_sqlite_bind(patched_app_client) -> None:
    """Already-bound empty wizard can still swap sqlite path via rebind."""
    client, srv, home = patched_app_client
    r = await client.post(
        "/api/setup/test-database",
        json={"driver": "sqlite", "sqlite_path": "data/wizard.db"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    r = await client.post(
        "/api/setup/database",
        json={"driver": "sqlite", "sqlite_path": "data/wizard.db"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["driver"] == "sqlite"

    cfg = load_config(home / "config.json")
    assert cfg.database.sqlite_path == "data/wizard.db"
    assert (home / "data" / "wizard.db").is_file()

    status = await client.get("/api/setup/status")
    assert status.json()["database_driver"] == "sqlite"
    assert status.json()["database_bound"] is True

    # Runtime stores must use the live pool (not a closed previous sqlite).
    assert srv.app_runtime is not None
    list(srv.app_runtime.agent_registry.providers.iter_usable_rows())
    assert srv.services.db is srv.app_runtime.agent_registry._providers._provider_repo._db  # noqa: SLF001


@pytest.mark.asyncio
async def test_deferred_verify_password_then_bind(tmp_octop_home: Path) -> None:
    """Greenfield: password works with no pool; /setup/database binds once."""
    async with octop_client(tmp_octop_home, bind_database=False) as (client, srv):
        assert srv.database_bound is False
        assert not (tmp_octop_home / "octop.db").exists()

        status = await client.get("/api/setup/status")
        assert status.status_code == 200
        body = status.json()
        assert body["setup_required"] is True
        assert body["database_bound"] is False
        assert body["database_driver"] is None

        health = await client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["db"] is False

        pw = read_password(tmp_octop_home.parent)
        assert pw is not None
        verified = await client.post("/api/setup/verify-password", json={"password": pw})
        assert verified.status_code == 200, verified.text
        tok = verified.json()["wizard_token"]

        # Admin requires a bound DB.
        early = await client.post(
            "/api/setup/initial-admin",
            json={"username": "admin", "password": "TestPass12"},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert early.status_code == 503
        assert early.json()["error"]["code"] == "SETUP_REQUIRED"

        applied = await client.post(
            "/api/setup/database",
            json={"driver": "sqlite", "sqlite_path": "octop.db"},
        )
        assert applied.status_code == 200, applied.text
        assert srv.database_bound is True
        assert (tmp_octop_home / "octop.db").is_file()

        status2 = await client.get("/api/setup/status")
        assert status2.json()["database_bound"] is True
        assert status2.json()["database_driver"] == "sqlite"

        created = await client.post(
            "/api/setup/initial-admin",
            json={"username": "admin", "password": "TestPass12"},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert created.status_code == 201, created.text


@pytest.mark.asyncio
async def test_setup_database_refuses_nonempty_target(patched_app_client) -> None:
    """Greenfield setup must not rebind onto a control-plane DB that already has users."""
    client, srv, home = patched_app_client
    _seed_occupied_sqlite(home)
    before = load_config(home / "config.json")

    r = await client.post(
        "/api/setup/database",
        json={"driver": "sqlite", "sqlite_path": "occupied.db"},
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "DATABASE_NOT_EMPTY"

    after = load_config(home / "config.json")
    assert after.database.sqlite_path == before.database.sqlite_path
    assert srv.user_manager.count() == 0

    tok = await wizard_token(client, home)
    created = await client.post(
        "/api/setup/initial-admin",
        json={"username": "admin", "password": "TestPass12"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert created.status_code == 201, created.text


@pytest.mark.asyncio
async def test_setup_database_refused_after_admin(patched_app_client) -> None:
    client, _srv, home = patched_app_client
    await bootstrap_admin(client, home)
    r = await client.post(
        "/api/setup/database",
        json={"driver": "sqlite", "sqlite_path": "octop.db"},
    )
    assert r.status_code == 410


@pytest.mark.parametrize("wire_http", [False, True])
async def test_history_rebind_rejection_preserves_restart_database(
    tmp_octop_home, monkeypatch, wire_http
):
    """A rejected change must not leave config pointing at a different database."""
    import socket
    from contextlib import asynccontextmanager

    from octop.infra.history.service import HistoryArchive
    from tests.support.app import write_octop_config
    from tests.support.loopback_http import loopback_http_client

    original_connect, original_dns = socket.socket.connect, socket.getaddrinfo

    def denied(*args, **kwargs):
        raise AssertionError("NON-TEST NETWORK FORBIDDEN")

    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)

    @asynccontextmanager
    async def client_for(server_client):
        if not wire_http:
            yield server_client
        else:
            async with loopback_http_client(
                server_client._octop_app, monkeypatch, original_connect, original_dns
            ) as wire:
                yield wire

    write_octop_config(tmp_octop_home, history_v2_enabled=True)
    async with (
        octop_client(tmp_octop_home) as (asgi, srv),
        client_for(asgi) as client,
    ):
        assert isinstance(srv.app_runtime.history_archive, HistoryArchive)
        before = srv.paths.config.read_bytes()
        old_db = srv.services.db
        response = await client.post(
            "/api/setup/database", json={"driver": "sqlite", "sqlite_path": "other.db"}
        )
        assert response.status_code == 400, response.text
        assert srv.paths.config.read_bytes() == before
        assert srv.services.db is old_db
        assert not (tmp_octop_home / "other.db").exists()
        await bootstrap_admin(client, tmp_octop_home)
        assert srv.user_manager.count() == 1
    async with octop_client(tmp_octop_home) as (_, restarted):
        assert restarted.user_manager.count() == 1
        assert restarted.database_bound


async def test_concurrent_database_submit_is_rejected_before_config_write(
    tmp_octop_home, monkeypatch
):
    import asyncio
    import socket

    from tests.support.loopback_http import loopback_http_client

    original_connect, original_dns = socket.socket.connect, socket.getaddrinfo

    def denied(*args, **kwargs):
        raise AssertionError("NON-TEST NETWORK FORBIDDEN")

    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    async with (
        octop_client(tmp_octop_home, bind_database=False) as (asgi, srv),
        loopback_http_client(
            asgi._octop_app, monkeypatch, original_connect, original_dns
        ) as client,
    ):
        entered, release = asyncio.Event(), asyncio.Event()
        original = srv.bind_control_plane
        calls = 0

        async def paused_bind():
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
            await original()

        monkeypatch.setattr(srv, "bind_control_plane", paused_bind)
        first = asyncio.create_task(
            client.post("/api/setup/database", json={"driver": "sqlite", "sqlite_path": "first.db"})
        )
        try:
            await asyncio.wait_for(entered.wait(), 5)
            second = await client.post(
                "/api/setup/database", json={"driver": "sqlite", "sqlite_path": "second.db"}
            )
            assert second.status_code == 410, second.text
            assert load_config(srv.paths.config).database.sqlite_path == "first.db"
            assert not (tmp_octop_home / "second.db").exists()
        finally:
            release.set()
            result = await asyncio.wait_for(first, 10)
        assert result.status_code == 200, result.text
        assert srv.services.config.database.sqlite_path == "first.db"
        await bootstrap_admin(client, tmp_octop_home)
    async with octop_client(tmp_octop_home) as (_, restarted):
        assert restarted.services.config.database.sqlite_path == "first.db"
        assert restarted.user_manager.count() == 1


async def test_stale_setup_worker_rejects_database_and_admin_mutations(tmp_octop_home):
    async with (
        octop_client(tmp_octop_home, bind_database=False) as (first, active),
        octop_client(tmp_octop_home, bind_database=False) as (second, stale),
    ):
        selected = await first.post(
            "/api/setup/database", json={"driver": "sqlite", "sqlite_path": "chosen.db"}
        )
        assert selected.status_code == 200, selected.text
        before = active.paths.config.read_bytes()
        for endpoint, payload in (
            ("database", {"driver": "sqlite", "sqlite_path": "stale.db"}),
            ("initial-admin", {"username": "stale", "password": "TestPass12"}),
            ("finish", {}),
        ):
            rejected = await second.post("/api/setup/" + endpoint, json=payload)
            assert rejected.status_code == 409, rejected.text
        assert active.paths.config.read_bytes() == before
        assert not (tmp_octop_home / "stale.db").exists()
        assert stale.database_bound is False
        await bootstrap_admin(first, tmp_octop_home)
        assert active.user_manager.count() == 1
    async with octop_client(tmp_octop_home) as (_, restarted):
        assert restarted.user_manager.count() == 1
        assert restarted.services.config.database.sqlite_path == "chosen.db"


async def test_database_config_write_failure_releases_claim_for_retry(tmp_octop_home, monkeypatch):
    from octop.infra.db import rebind

    async with octop_client(tmp_octop_home, bind_database=False) as (client, srv):
        before = srv.paths.config.read_bytes()
        with monkeypatch.context() as patch:

            def fail(*args):
                raise OSError("injected config write failure")

            patch.setattr(rebind, "_atomic_write_json", fail)
            with pytest.raises(OSError, match="injected config write failure"):
                await client.post(
                    "/api/setup/database", json={"driver": "sqlite", "sqlite_path": "chosen.db"}
                )
        assert srv.paths.config.read_bytes() == before
        assert not srv.database_bound
        retried = await client.post(
            "/api/setup/database", json={"driver": "sqlite", "sqlite_path": "chosen.db"}
        )
        assert retried.status_code == 200, retried.text
        await bootstrap_admin(client, tmp_octop_home)
    async with octop_client(tmp_octop_home) as (_, restarted):
        assert restarted.user_manager.count() == 1


async def test_post_link_crash_allows_setup_and_restart(tmp_octop_home, monkeypatch):
    import multiprocessing
    import socket

    from tests.support.loopback_http import loopback_http_client
    from tests.unit.test_config_creation import pause_after_link

    path = tmp_octop_home / "config.json"
    assert not path.exists()
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    child = ctx.Process(target=pause_after_link, args=(path, ready, release))
    child.start()
    try:
        assert ready.wait(10)
        child.terminate()
        child.join(5)
        assert not child.is_alive()
        assert path.stat().st_nlink == 2
    finally:
        if child.is_alive():
            child.terminate()
        child.join(5)

    original_connect, original_dns = socket.socket.connect, socket.getaddrinfo

    def denied(*args, **kwargs):
        raise AssertionError("NON-TEST NETWORK FORBIDDEN")

    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    async with (
        octop_client(tmp_octop_home) as (asgi, srv),
        loopback_http_client(
            asgi._octop_app, monkeypatch, original_connect, original_dns
        ) as client,
    ):
        assert path.stat().st_nlink == 1
        response = await client.post(
            "/api/setup/database", json={"driver": "sqlite", "sqlite_path": "recovered.db"}
        )
        assert response.status_code == 200, response.text
        created = await bootstrap_admin(client, tmp_octop_home)
        assert created.status_code == 201, created.text
        assert srv.user_manager.count() == 1
    async with octop_client(tmp_octop_home) as (_, restarted):
        assert restarted.user_manager.count() == 1
        assert restarted.services.config.database.sqlite_path == "recovered.db"
