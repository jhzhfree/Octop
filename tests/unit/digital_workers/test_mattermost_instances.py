"""Deployment metadata and offline read-only composition, with sockets forbidden."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from click.testing import CliRunner
from pydantic import SecretStr, ValidationError
from tests.unit.digital_workers.test_mattermost_intent import body, service
from tests.unit.digital_workers.test_mattermost_readiness import offline  # noqa: F401
from tests.unit.digital_workers.test_mattermost_readiness import setup as instance_setup

from octop.cli.commands.mattermost_instance import mattermost_instance
from octop.infra.db.migrate import run_migrations
from octop.infra.digital_workers.mattermost_instances import (
    CredentialBinding,
    InstanceConfiguration,
    configure_instance,
    observe_configured_identity,
)
from octop.infra.digital_workers.mattermost_verification import VerificationScope

setup = instance_setup

SECRET = "mock-test-secret-not-real"


def config(**changes):
    return InstanceConfiguration.model_validate(
        {
            "organization_id": "o",
            "instance_key": "enterprise-mm",
            "origin": "https://8.8.8.8",
            "bot_id": "b" * 26,
            "credential_ref": "worker-bot-v1",
            "status": "configured",
            **changes,
        }
    )


def scope(**changes):
    return VerificationScope(
        organization_id="o",
        instance_key="enterprise-mm",
        actor_id=1,
        worker_id="w",
        worker_version=1,
        intent_revision=1,
        configuration_revision=1,
        **changes,
    )


def provision(setup):
    service(setup).save_intent("o", "w", body())
    with setup[0].transaction() as c:
        c.execute("UPDATE users SET role='admin' WHERE id=3")
    assert (
        configure_instance(setup[1], actor_id=3, expected_revision=0, configuration=config()) == 1
    )


async def resolver(configuration, revision):
    return CredentialBinding(
        configuration=configuration, revision=revision, token=SecretStr(SECRET)
    )


def response(request):
    assert request.method == "GET" and str(request.url) == "https://8.8.8.8/api/v4/users/me"
    assert request.headers["authorization"] == "Bearer " + SECRET
    return httpx.Response(
        200, json={"id": "b" * 26, "is_bot": True, "delete_at": 0, "roles": "system_user"}
    )


async def observe(setup, **kwargs):
    return await observe_configured_identity(
        setup[1],
        scope(),
        resolve_credential=kwargs.pop("resolve_credential", resolver),
        transport=httpx.MockTransport(kwargs.pop("handler", response)),
        **kwargs,
    )


def test_only_enabled_database_admin_can_configure(setup):
    for actor in (1, 2, 3, 999):
        assert (
            configure_instance(
                setup[1], actor_id=actor, expected_revision=0, configuration=config()
            )
            is None
        )
    with setup[0].transaction() as c:
        c.execute("UPDATE users SET role='admin',disabled=1 WHERE id=3")
    assert (
        configure_instance(setup[1], actor_id=3, expected_revision=0, configuration=config())
        is None
    )
    with setup[0].connect() as c:
        assert c.execute("SELECT COUNT(*) FROM mattermost_instances").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_configuration_cas_tombstone_rotation_and_org_isolation(setup):
    provision(setup)
    repo = setup[1].mattermost_instance_repo
    assert repo.current_configuration(**scope().model_dump())["revision"] == 1
    assert (
        repo.current_configuration(**{**scope().model_dump(), "organization_id": "other"}) is None
    )
    assert (
        configure_instance(setup[1], actor_id=3, expected_revision=0, configuration=config())
        is None
    )
    assert (
        configure_instance(
            setup[1], actor_id=3, expected_revision=1, configuration=config(status="revoked")
        )
        == 2
    )
    assert repo.current_configuration(**scope().model_dump()) is None
    assert (
        configure_instance(setup[1], actor_id=3, expected_revision=1, configuration=config())
        is None
    )
    assert (
        configure_instance(
            setup[1],
            actor_id=3,
            expected_revision=2,
            configuration=config(credential_ref="rotated"),
        )
        == 3
    )
    assert repo.current_configuration(**scope().model_dump()) is None
    assert (
        repo.current_configuration(**{**scope().model_dump(), "configuration_revision": 3})[
            "credential_ref"
        ]
        == "rotated"
    )
    run_migrations(setup[0])
    with setup[0].connect() as c:
        assert c.execute("SELECT revision FROM mattermost_instances").fetchone()[0] == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"origin": "https://mm.example.com"},
        {"origin": "http://8.8.8.8"},
        {"origin": "https://127.0.0.1"},
        {"origin": "https://8.8.8.8/path"},
        {"credential_ref": "../../secret"},
        {"token": SECRET},
    ],
)
def test_manifest_rejects_untrusted_shapes(changes):
    with pytest.raises(ValidationError):
        config(**changes)


async def test_observation_audit_and_database_are_credential_free(setup, caplog):
    provision(setup)
    result = await observe(setup)
    assert result.result == "bot_identity_observed"
    assert (
        not result.execution_authorized
        and not result.automatic_send_allowed
        and not result.human_initiated_send_allowed
    )
    assert SECRET not in result.model_dump_json() and SECRET not in caplog.text
    with setup[0].connect() as c:
        dump = "\n".join(c.iterdump())
        assert SECRET not in dump
        payload = json.loads(
            c.execute("SELECT payload FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()[0]
        )
        assert payload["result"] == "bot_identity_observed"
        assert payload["consistency"] == "observation_not_atomic"
        assert c.execute("SELECT status FROM worker_mattermost_intents").fetchone()[0] == "draft"


MUTATIONS = [
    "UPDATE users SET disabled=1 WHERE id=1",
    "DELETE FROM organization_members WHERE user_id=1 AND organization_id='o'",
    "UPDATE organization_members SET role='member' WHERE user_id=1 AND organization_id='o'",
    "UPDATE agents SET user_id=2,is_shared=0 WHERE agent_id='a'",
    "UPDATE agents SET enabled=0 WHERE agent_id='a'",
    "UPDATE digital_workers SET status='paused' WHERE worker_id='w'",
    "UPDATE digital_workers SET version=version+1 WHERE worker_id='w'",
    "DELETE FROM organization_assignments",
    "UPDATE worker_mattermost_intents SET status='revoked',revision=revision+1",
    "UPDATE worker_mattermost_intents SET bot_id='cccccccccccccccccccccccccc'",
    "UPDATE mattermost_instances SET status='revoked',revision=revision+1",
    "UPDATE mattermost_instances SET revision=revision+1",
]


@pytest.mark.parametrize("sql", MUTATIONS)
@pytest.mark.parametrize("phase", ["before", "credential", "response"])
async def test_revocations_block_before_or_reject_after_request(setup, sql, phase):
    provision(setup)
    calls = []
    secrets = []

    def mutate():
        with setup[0].transaction() as c:
            c.execute(sql)

    async def resolving(configuration, revision):
        secrets.append(True)
        if phase == "credential":
            mutate()
        return await resolver(configuration, revision)

    def handler(request):
        calls.append(True)
        if phase == "response":
            mutate()
        return response(request)

    if phase == "before":
        mutate()
    result = await observe(setup, resolve_credential=resolving, handler=handler)
    assert result.result == "access_changed"
    assert len(calls) == (1 if phase == "response" else 0)
    assert len(secrets) == (0 if phase == "before" else 1)


@pytest.mark.parametrize("change", ["origin", "reference", "revision"])
async def test_provider_must_bind_exact_target_and_revision(setup, change):
    provision(setup)

    async def wrong(configuration, revision):
        if change == "origin":
            configuration = config(origin="https://1.1.1.1")
        elif change == "reference":
            configuration = config(credential_ref="other")
        else:
            revision += 1
        return await resolver(configuration, revision)

    def no_network(request):
        pytest.fail("mismatched credential reached request")

    assert (
        await observe(setup, resolve_credential=wrong, handler=no_network)
    ).result == "access_changed"


async def test_provider_error_is_redacted(setup, caplog):
    provision(setup)

    async def fail(*args):
        raise RuntimeError(SECRET)

    result = await observe(setup, resolve_credential=fail)
    assert result.result == "unavailable"
    assert SECRET not in caplog.text + result.model_dump_json()


def test_configuration_and_audit_are_atomic(setup):
    provision(setup)
    with setup[0].transaction() as c:
        c.execute(
            "CREATE TRIGGER deny_mm_audit BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT,'audit unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        configure_instance(
            setup[1], actor_id=3, expected_revision=1, configuration=config(status="revoked")
        )
    assert (
        setup[1].mattermost_instance_repo.current_configuration(**scope().model_dump())["revision"]
        == 1
    )


async def test_final_audit_rechecks_scope(setup, monkeypatch):
    provision(setup)
    original = setup[1].mattermost_instance_repo.audit_observation

    def racing(**kwargs):
        with setup[0].transaction() as c:
            c.execute("UPDATE users SET disabled=1 WHERE id=1")
        return original(**kwargs)

    monkeypatch.setattr(setup[1].mattermost_instance_repo, "audit_observation", racing)
    assert (await observe(setup)).result == "access_changed"


async def test_production_transport_is_not_available(setup):
    provision(setup)
    with pytest.raises(ValueError, match="offline transport required"):
        await observe_configured_identity(
            setup[1], scope(), resolve_credential=resolver, transport=None
        )


def test_cli_manifest_provision_and_sanitized_errors(setup, tmp_path, monkeypatch):
    with setup[0].transaction() as c:
        c.execute("UPDATE users SET role='admin' WHERE id=3")

    @contextmanager
    def services():
        yield SimpleNamespace(repos=setup[1])

    monkeypatch.setattr("octop.cli.support.db.open_cli_services", services)
    manifest = tmp_path / "instance.json"
    manifest.write_text(config().model_dump_json())
    args = ["--manifest", str(manifest), "--actor-id", "3", "--expected-revision", "0"]
    result = CliRunner().invoke(mattermost_instance, args)
    assert result.exit_code == 0 and json.loads(result.output) == {
        "revision": 1,
        "status": "configured",
        "connected": False,
    }
    manifest.write_text(json.dumps({**config().model_dump(), "token": SECRET}))
    result = CliRunner().invoke(mattermost_instance, args)
    assert result.exit_code != 0 and SECRET not in result.output
    assert "credential_ref" not in result.output


def test_two_connections_config_cas_single_winner(setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from octop.infra.db.pool import SqlitePool
    from octop.infra.db.services import RepoBundle

    provision(setup)
    second = SqlitePool(setup[0].path)
    barrier = Barrier(2)

    def write(repos):
        barrier.wait()
        return configure_instance(repos, actor_id=3, expected_revision=1, configuration=config())

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(write, repos) for repos in (setup[1], RepoBundle.from_pool(second))
            ]
            assert sorted(str(f.result(timeout=10)) for f in futures) == ["2", "None"]
    finally:
        second.close()


async def test_admin_role_is_database_current_not_claimed(setup):
    provision(setup)
    admin_scope = VerificationScope.model_validate({**scope().model_dump(), "actor_id": 3})
    assert (
        setup[1].mattermost_instance_repo.current_configuration(**admin_scope.model_dump())
        is not None
    )
    with setup[0].transaction() as c:
        c.execute("UPDATE users SET role='user' WHERE id=3")
    assert (
        setup[1].mattermost_instance_repo.current_configuration(**admin_scope.model_dump()) is None
    )
    assert (
        configure_instance(setup[1], actor_id=3, expected_revision=1, configuration=config())
        is None
    )


async def test_no_success_return_when_observation_audit_fails(setup):
    provision(setup)
    with setup[0].transaction() as c:
        c.execute(
            "CREATE TRIGGER deny_mm_audit BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT,'audit unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        await observe(setup)


def test_upgrade_from_real_v39_and_repeat_preserves_data(tmp_path, monkeypatch):
    from octop.infra.db import migrate
    from octop.infra.db.pool import SqlitePool

    db = SqlitePool(tmp_path / "upgrade.db")
    discover = migrate._discover
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                migrate,
                "_discover",
                lambda dialect="sqlite": [(n, path) for n, path in discover(dialect) if n <= 39],
            )
            run_migrations(db)
        with db.transaction() as c:
            assert c.execute("SELECT version FROM _schema_version").fetchone()[0] == 39
            c.execute("INSERT INTO settings(key,value) VALUES('mm-upgrade-sentinel','unchanged')")
        run_migrations(db)
        run_migrations(db)
        with db.connect() as c:
            assert c.execute("SELECT version FROM _schema_version").fetchone()[0] == 40
            assert (
                c.execute("SELECT value FROM settings WHERE key='mm-upgrade-sentinel'").fetchone()[
                    0
                ]
                == "unchanged"
            )
            assert c.execute("SELECT COUNT(*) FROM mattermost_instances").fetchone()[0] == 0
            assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        # Both dialects ship the same portable DDL. PostgreSQL runtime remains untested here.
        sqlite = dict(discover("sqlite"))[40].read_text()
        postgres = dict(discover("postgresql"))[40].read_text()
        assert sqlite == postgres
    finally:
        db.close()
