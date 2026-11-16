"""Real ASGI/SQLite/signature boundary; synthetic keys and source observation only."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI

from octop.api.routers.mattermost_ingress import create_router
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.mattermost_ingress import MattermostIngressRepo
from octop.infra.digital_workers.mattermost_ingress import (
    BROKER_DOMAIN,
    MattermostIngressService,
    SourceObservation,
)
from octop.infra.digital_workers.mattermost_proof import DOMAIN, verify_proof
from tests.integration.test_scene_execution_offline import scene_runtime as scene_runtime
from tests.unit.digital_workers.test_mattermost_proof import VECTORS, wire

PATH = "/api/integrations/mattermost/v1/intakes"


@pytest.fixture
def ingress(scene_runtime):
    db = scene_runtime.db
    scene_runtime.repos.user_repo.set_permissions(1, ["tasks"])
    broker_key = Ed25519PrivateKey.generate()
    proof_key = Ed25519PrivateKey.generate()
    claims = json.loads(
        base64.b64decode(json.loads(base64.b64decode(VECTORS[0]["Proof"]))["payload"])
    )
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO mattermost_brokers VALUES (?,?,1,1,'o','w',1,1,?,?,?)",
            (
                "fixture-broker",
                broker_key.public_key().public_bytes_raw().hex(),
                claims["key_id"],
                proof_key.public_key().public_bytes_raw().hex(),
                json.dumps(claims["intent"]["binding"]),
            ),
        )
    repo = MattermostIngressRepo(db)
    state = {
        "now": 101,
        "source_state": "awaiting_octop_authorization",
        "version": 1,
        "hook": None,
        "calls": 0,
    }

    async def source(mapping, receipt):
        state["calls"] += 1
        assert mapping["broker_id"] == "fixture-broker"
        assert receipt == claims["intent"]["receipt"]
        if state["hook"]:
            state["hook"]()
        candidate = state["candidate"]
        return SourceObservation(
            delivery_key=candidate.delivery_key,
            fingerprint=candidate.fingerprint,
            commitment=candidate.source_commitment(),
            state=state["source_state"],
            receipt_expires_at=candidate.intent.receipt_expires_at,
            version=state["version"],
        )

    service = MattermostIngressService(repo, source=source, clock=lambda: state["now"])

    def proof():
        payload = wire(claims)
        raw = wire(
            {
                "payload": base64.b64encode(payload).decode(),
                "signature": base64.b64encode(proof_key.sign(DOMAIN + payload)).decode(),
            }
        )
        state["candidate"] = verify_proof(
            raw, service._policy(repo.mapping("fixture-broker")), now=101
        )
        return raw

    raw = proof()

    def headers(
        method="POST",
        path=PATH,
        body=raw,
        *,
        nonce=None,
        key=broker_key,
        timestamp=None,
        broker="fixture-broker",
    ):
        timestamp = str(state["now"] if timestamp is None else timestamp)
        nonce = nonce or (uuid4().hex + uuid4().hex)
        message = (
            BROKER_DOMAIN
            + "\n".join(
                (broker, timestamp, nonce, method, path, hashlib.sha256(body).hexdigest())
            ).encode()
        )
        return {
            "x-octop-broker": broker,
            "x-octop-broker-time": timestamp,
            "x-octop-broker-nonce": nonce,
            "x-octop-broker-signature": base64.b64encode(key.sign(message)).decode(),
            "content-type": "application/octet-stream",
        }

    app = FastAPI()
    app.include_router(create_router(service))
    return SimpleNamespace(
        db=db,
        broker_key=broker_key,
        repo=repo,
        service=service,
        state=state,
        raw=raw,
        claims=claims,
        proof=proof,
        headers=headers,
        app=app,
        key=state["candidate"].delivery_key,
    )


async def send(env, method="POST", path=PATH, body=None, headers=None):
    body = env.raw if body is None and method == "POST" and path == PATH else body or b""
    headers = env.headers(method, path, body) if headers is None else headers
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url="https://fixture.invalid"
    ) as client:
        response = await client.request(method, path, content=body, headers=headers)
    assert response.headers["cache-control"] == "no-store"
    return response


def count(env, table):
    assert table in {
        "mattermost_intakes",
        "mattermost_broker_nonces",
        "audit_log",
        "enterprise_scene_runs",
    }
    with env.db.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]


def cancellation(env, revision=1):
    path = PATH + "/" + env.key + "/cancellations"
    body = json.dumps({"operation_id": "b" * 64, "expected_revision": revision}).encode()
    return path, body, env.headers("POST", path, body) | {"content-type": "application/json"}


@pytest.mark.asyncio
async def test_accept_duplicate_query_cancel_and_terminal_fence(ingress):
    e = ingress
    first = await send(e)
    assert first.status_code == 201
    assert first.json()["execution_authorized"] is False
    assert first.json()["source_version"] == "1"
    assert (await send(e)).status_code == 200
    assert count(e, "mattermost_intakes") == 1
    assert count(e, "enterprise_scene_runs") == 0
    query = await send(e, "GET", PATH + "/" + e.key)
    assert query.json() == first.json()
    assert not any(k in query.json() for k in ("goal", "proof", "receipt", "actor_id"))
    e.state.update(source_state="cancelled", version=2)
    path, body, headers = cancellation(e)
    result = await send(e, "POST", path, body, headers)
    assert result.status_code == 200 and result.json()["state"] == "cancelled"
    assert result.json()["revision"] == 2
    path, body, headers = cancellation(e)
    assert (await send(e, "POST", path, body, headers)).json() == result.json()
    e.state["source_state"] = "awaiting_octop_authorization"
    assert (await send(e)).status_code == 410
    e.state["now"] = 1000  # Query works without expired proof or source lookup.
    e.state["hook"] = lambda: pytest.fail("query must not use source proof")
    assert (await send(e, "GET", PATH + "/" + e.key)).json()["state"] == "cancelled"
    with e.db.connect() as conn:
        rows = conn.execute(
            "SELECT actor,payload FROM audit_log WHERE action LIKE 'mattermost.intake.%'"
        ).fetchall()
    assert len(rows) == 2
    assert all(row["actor"] == "broker:fixture-broker" for row in rows)
    assert all("goal" not in row["payload"] for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "jwt",
        "proof_only",
        "wrong_key",
        "method",
        "path",
        "body",
        "expired",
        "future",
        "unknown_broker",
    ],
)
async def test_broker_authentication_cannot_be_replaced(ingress, bad):
    e = ingress
    headers = e.headers()
    if bad == "jwt":
        headers = {"authorization": "Bearer synthetic-user-jwt"}
    elif bad == "proof_only":
        headers = {}
    elif bad == "wrong_key":
        headers = e.headers(key=Ed25519PrivateKey.generate())
    elif bad == "method":
        headers = e.headers(method="GET")
    elif bad == "path":
        headers = e.headers(path=PATH + "/other")
    elif bad == "body":
        headers = e.headers(body=e.raw + b" ")
    elif bad == "expired":
        headers = e.headers(timestamp=71)
    elif bad == "future":
        headers = e.headers(timestamp=102)
    else:
        headers = e.headers(broker="unknown")
    assert (await send(e, headers=headers)).status_code == 401
    assert count(e, "mattermost_intakes") == 0 and e.state["calls"] == 0


@pytest.mark.asyncio
async def test_nonce_replay_and_source_change_on_duplicate(ingress):
    e = ingress
    headers = e.headers()
    assert (await send(e, headers=headers)).status_code == 201
    assert (await send(e, headers=headers)).status_code == 409
    e.state["source_state"] = "cancelled"
    assert (await send(e)).status_code == 403
    assert count(e, "mattermost_intakes") == 1


@pytest.mark.asyncio
async def test_fingerprint_conflict_no_overwrite(ingress):
    e = ingress
    first = (await send(e)).json()
    e.claims["intent"]["goal"] = "different untrusted goal"
    raw = e.proof()
    assert (await send(e, body=raw, headers=e.headers(body=raw))).status_code == 409
    assert (await send(e, "GET", PATH + "/" + e.key)).json() == first


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["broker", "mapping", "worker", "member", "user", "tasks", "assignment"]
)
async def test_revocation_during_source_lookup_blocks_commit(ingress, mutation):
    e = ingress

    def revoke():
        statements = {
            "broker": "UPDATE mattermost_brokers SET enabled=0",
            "mapping": "UPDATE mattermost_brokers SET revision=revision+1",
            "worker": "UPDATE digital_workers SET version=version+1",
            "member": "DELETE FROM organization_members WHERE user_id=1",
            "user": "UPDATE users SET disabled=1 WHERE id=1",
            "tasks": "UPDATE users SET permissions='[]' WHERE id=1",
            "assignment": "DELETE FROM organization_assignments",
        }
        with e.db.transaction() as conn:
            conn.execute(statements[mutation])

    e.state["hook"] = revoke
    assert (await send(e)).status_code == 403
    assert count(e, "mattermost_intakes") == 0


@pytest.mark.asyncio
async def test_intake_and_audit_rollback_together(ingress):
    e = ingress
    with e.db.connect() as conn:
        conn.execute("""CREATE TRIGGER fail_intake_audit BEFORE INSERT ON audit_log
            WHEN NEW.action='mattermost.intake.accepted_pending'
            BEGIN SELECT RAISE(ABORT,'synthetic audit failure'); END""")
    result = await send(e)
    assert result.status_code == 503 and "synthetic" not in result.text
    assert count(e, "mattermost_intakes") == 0
    # Auth nonce remains consumed: a failed/unknown write cannot reuse its signed request.
    assert count(e, "mattermost_broker_nonces") == 1
    assert (await send(e, "GET", PATH + "/" + e.key)).status_code == 404


@pytest.mark.asyncio
async def test_cancel_requires_current_source_and_cas(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    path, body, headers = cancellation(e)
    assert (await send(e, "POST", path, body, headers)).status_code == 403
    e.state.update(source_state="cancelled", version=2)
    path, body, headers = cancellation(e, revision=2)
    assert (await send(e, "POST", path, body, headers)).status_code == 409
    with e.db.connect() as conn:
        conn.execute("""CREATE TRIGGER fail_cancel_audit BEFORE INSERT ON audit_log
            WHEN NEW.action='mattermost.intake.cancelled'
            BEGIN SELECT RAISE(ABORT,'synthetic cancellation audit failure'); END""")
    path, body, headers = cancellation(e)
    assert (await send(e, "POST", path, body, headers)).status_code == 503
    assert (await send(e, "GET", PATH + "/" + e.key)).json()["state"] == "pending_authorization"


@pytest.mark.asyncio
async def test_missing_composition_denies_before_database_or_source(ingress):
    e = ingress
    e.service.source = None
    assert (await send(e)).status_code == 503
    e.app = FastAPI()
    e.app.include_router(create_router())
    assert (await send(e)).status_code == 503
    assert count(e, "mattermost_broker_nonces") == e.state["calls"] == 0


@pytest.mark.asyncio
async def test_cross_connection_concurrent_admission_and_restart(ingress):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    e = ingress
    mapping = e.repo.mapping("fixture-broker")
    c = e.state["candidate"]
    values = {
        "delivery_key": c.delivery_key,
        "fingerprint": c.fingerprint,
        "receipt": c.intent.receipt,
        "commitment": c.source_commitment(),
        "source_expires_at": c.intent.receipt_expires_at,
        "source_version": 1,
    }
    barrier = Barrier(2)

    def accept(_):
        db = SqlitePool(e.db.path)
        try:
            barrier.wait(timeout=10)
            return MattermostIngressRepo(db).accept(
                mapping, values, issued=100, expires=160, clock=lambda: 101
            )
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(accept, range(2)))
    assert sorted(created for _, created in results) == [False, True]
    db = SqlitePool(e.db.path)
    try:
        assert MattermostIngressRepo(db).inspect(mapping, e.key)["state"] == "pending_authorization"
    finally:
        db.close()
    assert count(e, "mattermost_intakes") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["source_error", "expired", "rewound", "wrong_identity"])
async def test_source_failure_or_late_proof_never_persists(ingress, fault):
    e = ingress

    def change():
        if fault == "source_error":
            raise RuntimeError("SECRET remote error")
        if fault == "expired":
            e.state["now"] = 160
        elif fault == "rewound":
            e.state["now"] = 100
        elif fault == "wrong_identity":
            e.state["candidate"] = e.state["candidate"].model_copy(update={"fingerprint": "f" * 64})

    e.state["hook"] = change
    response = await send(e)
    assert response.status_code in (403, 422, 503) and "SECRET" not in response.text
    assert count(e, "mattermost_intakes") == 0


@pytest.mark.asyncio
async def test_another_authenticated_broker_cannot_query_or_cancel(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    other = Ed25519PrivateKey.generate()
    with e.db.transaction() as conn:
        conn.execute(
            """INSERT INTO mattermost_brokers
            SELECT 'another',?,enabled,revision,organization_id,worker_id,actor_id,worker_version,
            proof_key_id,proof_public_key,binding_json FROM mattermost_brokers WHERE broker_id='fixture-broker'""",
            (other.public_key().public_bytes_raw().hex(),),
        )
    path = PATH + "/" + e.key
    assert (
        await send(e, "GET", path, headers=e.headers("GET", path, b"", key=other, broker="another"))
    ).status_code == 403
    path, body, _ = cancellation(e)
    headers = e.headers("POST", path, body, key=other, broker="another") | {
        "content-type": "application/json"
    }
    assert (await send(e, "POST", path, body, headers)).status_code == 403


@pytest.mark.asyncio
async def test_cancel_must_advance_source_version_and_expiry_is_terminal(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    e.state["source_state"] = "cancelled"
    path, body, headers = cancellation(e)
    assert (await send(e, "POST", path, body, headers)).status_code == 409
    e.state.update(version=2, now=161)
    path, body, headers = cancellation(e)
    response = await send(e, "POST", path, body, headers)
    assert response.status_code == 200 and response.json()["state"] == "expired"
    assert (await send(e, "GET", PATH + "/" + e.key)).json()["state"] == "expired"


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "linux", reason="Linux recovery image isolation")
async def test_recovery_image_disables_broker_trust(ingress, tmp_path):
    import sqlite3

    from octop.config import DatabaseConfig
    from octop.infra.backup.recovery_image import create_recovery_image
    from octop.infra.backup.system_archive import create_system_backup
    from octop.infra.utils.paths import PathLayout

    archive = tmp_path / "synthetic.tar.gz"
    create_system_backup(
        paths=PathLayout(tmp_path / "layout"),
        agent_rows=[],
        pool=ingress.db,
        db_config=DatabaseConfig(),
        dest=archive,
        include_config=False,
        include_workspaces=False,
        include_skill_packages=False,
        include_plugins=False,
        include_knowledge=False,
    )
    target = tmp_path / "quarantined"
    create_recovery_image(
        archive,
        target,
        identity_policy="quarantine-all",
        expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
    )
    with sqlite3.connect(target / "octop.db") as conn:
        assert conn.execute("SELECT enabled FROM mattermost_brokers").fetchone()[0] == 0
    assert ingress.repo.mapping("fixture-broker")["enabled"] == 1


@pytest.mark.asyncio
async def test_changed_source_mapping_cannot_query_retained_identity(ingress):
    e = ingress
    assert (await send(e)).status_code == 201
    changed = dict(e.claims["intent"]["binding"], instance="another-instance")
    with e.db.transaction() as conn:
        conn.execute(
            "UPDATE mattermost_brokers SET binding_json=?,revision=revision+1",
            (json.dumps(changed),),
        )
    assert (await send(e, "GET", PATH + "/" + e.key)).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b'{"operation_id":"' + b"a" * 64 + b'","expected_revision":true}',
        b'{"expected_revision":1,"expected_revision":1}',
        b'{"actor_id":1}',
    ],
)
async def test_cancel_payload_rejected_without_echo(ingress, body):
    e = ingress
    assert (await send(e)).status_code == 201
    path = PATH + "/" + e.key + "/cancellations"
    headers = e.headers("POST", path, body) | {"content-type": "application/json"}
    result = await send(e, "POST", path, body, headers)
    assert result.status_code == 422
    assert result.json() == {"code": "INTAKE_REQUEST_INVALID"}


@pytest.mark.asyncio
async def test_commit_acknowledgement_loss_reconciles_original_identity(ingress, monkeypatch):
    e = ingress
    original = e.repo.accept

    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic lost acknowledgement")

    monkeypatch.setattr(e.repo, "accept", lost)
    response = await send(e)
    assert response.status_code == 503 and response.json()["code"] == "INTAKE_OUTCOME_UNKNOWN"
    assert e.state["calls"] == 1  # No automatic re-delivery.
    query = await send(e, "GET", PATH + "/" + e.key)
    assert query.status_code == 200 and query.json()["delivery_key"] == e.key
    assert query.json()["state"] == "pending_authorization"
    assert count(e, "mattermost_intakes") == 1


@pytest.mark.asyncio
async def test_two_connections_cancel_once(ingress):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    e = ingress
    assert (await send(e)).status_code == 201
    mapping = e.repo.mapping("fixture-broker")
    observed = e.repo.inspect(mapping, e.key)
    barrier = Barrier(2)

    def cancel(index):
        db = SqlitePool(e.db.path)
        try:
            barrier.wait(timeout=10)
            return MattermostIngressRepo(db).cancel(
                mapping, observed, 1, str(index) * 64, 2, lambda: 101
            )
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(cancel, (1, 2)))
    assert all(row["state"] == "cancelled" and row["revision"] == 2 for row in results)
    with e.db.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action='mattermost.intake.cancelled'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.asyncio
async def test_reused_proof_key_is_not_a_separate_broker_identity(ingress):
    e = ingress
    with e.db.transaction() as conn:
        conn.execute("UPDATE mattermost_brokers SET broker_public_key=proof_public_key")
    assert (await send(e)).status_code == 503
    assert count(e, "mattermost_broker_nonces") == count(e, "mattermost_intakes") == 0


@pytest.mark.asyncio
async def test_source_high_water_cross_connection_competition(ingress):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from octop.infra.db.repos.mattermost_ingress import IntakeError

    e = ingress
    assert (await send(e)).status_code == 201
    mapping = e.repo.mapping("fixture-broker")
    row = e.repo.inspect(mapping, e.key)
    barrier = Barrier(2)

    def observe(version):
        db = SqlitePool(e.db.path)
        try:
            barrier.wait(timeout=10)
            try:
                MattermostIngressRepo(db).observe_source(mapping, row, version, lambda: 101)
                return "observed"
            except IntakeError as exc:
                return exc.code
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(observe, (2, 10)))
    assert outcomes[1] == "observed"
    assert outcomes[0] in ("observed", "SOURCE_VERSION_REWOUND")
    db = SqlitePool(e.db.path)
    try:
        stored = MattermostIngressRepo(db).inspect(mapping, e.key)
        assert stored["observed_source_version"] == 10
        assert stored["source_version"] == stored["revision"] == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_higher_observation_between_source_lookup_and_cancel_wins(ingress, monkeypatch):
    e = ingress
    assert (await send(e)).status_code == 201
    original = e.repo.cancel

    def race(mapping, observed, *args):
        db = SqlitePool(e.db.path)
        try:
            MattermostIngressRepo(db).observe_source(mapping, observed, 10, lambda: 101)
        finally:
            db.close()
        return original(mapping, observed, *args)

    monkeypatch.setattr(e.repo, "cancel", race)
    e.state.update(version=3, source_state="cancelled")
    path, body, headers = cancellation(e)
    assert (await send(e, "POST", path, body, headers)).status_code == 409
    row = (await send(e, "GET", PATH + "/" + e.key)).json()
    assert row["source_version"] == "10" and row["state"] == "pending_authorization"


@pytest.mark.asyncio
async def test_schema_46_fences_unrecoverable_legacy_observation_history(ingress):
    from octop.infra.db.migrate import run_migrations

    e = ingress
    assert (await send(e)).status_code == 201
    with e.db.transaction() as conn:
        conn.execute("DROP TABLE mattermost_retired_intakes")
        conn.execute("ALTER TABLE mattermost_intakes DROP COLUMN observed_source_version")
        conn.execute("UPDATE mattermost_intakes SET source_version=3")
        conn.execute("UPDATE _schema_version SET version=45")
    run_migrations(e.db)
    with e.db.connect() as conn:
        row = conn.execute(
            "SELECT source_version,observed_source_version,state,revision FROM mattermost_intakes"
        ).fetchone()
        assert tuple(row) == (3, 3, "pending_authorization", 1)
        assert conn.execute("SELECT enabled FROM mattermost_brokers").fetchone()[0] == 0
    assert (await send(e, "GET", PATH + "/" + e.key)).status_code == 401


@pytest.mark.asyncio
async def test_schema_46_failure_rolls_back_column_fence_and_version(ingress):
    import sqlite3

    from octop.infra.db.migrate import run_migrations

    e = ingress
    assert (await send(e)).status_code == 201
    with e.db.transaction() as conn:
        conn.execute("DROP TABLE mattermost_retired_intakes")
        conn.execute("ALTER TABLE mattermost_intakes DROP COLUMN observed_source_version")
        conn.execute("UPDATE _schema_version SET version=45")
        conn.execute(
            "CREATE TRIGGER fail_watermark_migration BEFORE UPDATE ON _schema_version "
            "WHEN NEW.version=46 BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        run_migrations(e.db)
    with e.db.connect() as conn:
        assert not conn.in_transaction
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 45
        assert conn.execute("SELECT enabled FROM mattermost_brokers").fetchone()[0] == 1
        assert "observed_source_version" not in {
            row[1] for row in conn.execute("PRAGMA table_info(mattermost_intakes)")
        }
        conn.execute("DROP TRIGGER fail_watermark_migration")
    run_migrations(e.db)
    with e.db.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 48
        assert conn.execute("SELECT enabled FROM mattermost_brokers").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_higher_observation_between_lookup_and_duplicate_accept_wins(ingress, monkeypatch):
    e = ingress
    assert (await send(e)).status_code == 201
    original = e.repo.accept

    def race(mapping, values, **kwargs):
        db = SqlitePool(e.db.path)
        try:
            MattermostIngressRepo(db).observe_source(mapping, values, 10, lambda: 101)
        finally:
            db.close()
        return original(mapping, values, **kwargs)

    monkeypatch.setattr(e.repo, "accept", race)
    e.state.update(version=3)
    assert (await send(e)).status_code == 409
    row = (await send(e, "GET", PATH + "/" + e.key)).json()
    assert row["source_version"] == "10"
    assert row["state_source_version"] == "1" and row["revision"] == 1
