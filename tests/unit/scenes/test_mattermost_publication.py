"""Real SQLite, local report/approval ledger. No network or actual Mattermost."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.unit.digital_workers.test_mattermost_instances import provision
from tests.unit.digital_workers.test_mattermost_readiness import (
    offline,  # noqa: F401
)
from tests.unit.digital_workers.test_mattermost_readiness import (
    setup as instance_setup,
)

from octop.infra.db.repos.scene_acceptance import SceneAcceptanceRepo, snapshot_hash
from octop.infra.scenes.mattermost_publication import MattermostPublicationService
from octop.infra.scenes.service import SceneError

setup = instance_setup

CONTENT = "Inspection report: service healthy. INTERNAL_REPORT_SENTINEL"
REPORT_HASH = hashlib.sha256(CONTENT.encode()).hexdigest()


def preview_args():
    return {
        "expected_report_sha256": REPORT_HASH,
        "expected_worker_version": 1,
        "expected_intent_revision": 1,
        "expected_configuration_revision": 1,
    }


def publication_service(setup, actor=1):
    return MattermostPublicationService(setup[1], actor)


@pytest.fixture
def publication_setup(setup):
    provision(setup)
    db, _ = setup
    with db.transaction() as c:
        c.execute("UPDATE users SET permissions='[\"tasks\"]'")
        c.execute("UPDATE organization_members SET role='manager' WHERE user_id=2")
        c.execute("UPDATE agents SET is_shared=1")
        c.execute(
            "INSERT INTO settings(key,value) VALUES(?,?)",
            (
                "enterprise_assistant.features.v1",
                json.dumps({"enabled": True, "reports": True, "inspection": True}),
            ),
        )
        c.execute(
            "INSERT INTO enterprise_scenes(scene_id,organization_id,position_id,agent_id,owner_user_id,name,prompt,created_at,updated_at) VALUES('s','o','p','a',1,'Inspection','inspect',1,1)"
        )
        c.execute(
            "INSERT INTO enterprise_scene_runs(run_id,scene_id,organization_id,position_id,agent_id,scene_name,prompt_snapshot,requested_by,approved_by,status,created_at,updated_at,plan_snapshot_json,inspection_report_sha256) VALUES('r','s','o','p','a','Inspection','inspect',1,2,'succeeded',1,1,?,?)",
            (json.dumps({"worker": {"worker_id": "w"}}), REPORT_HASH),
        )
        c.execute(
            "INSERT INTO scene_inspection_batches(run_id,status,sources_json,content,content_sha256,created_at,updated_at) VALUES('r','reported','[]',?,?,1,1)",
            (CONTENT, REPORT_HASH),
        )
        run = dict(c.execute("SELECT * FROM enterprise_scene_runs WHERE run_id='r'").fetchone())
    SceneAcceptanceRepo(db).decide(
        organization_id="o",
        run_id="r",
        reviewer_id=2,
        decision="accepted",
        evidence_sha256=REPORT_HASH,
        evidence_length=len(CONTENT),
        expected_snapshot_hash=snapshot_hash(run),
    )
    yield setup


def propose(setup):
    return publication_service(setup).preview("o", "r", **preview_args())


def approve(setup, actor=2):
    draft = propose(setup)
    return publication_service(setup, actor).approve(
        "o", "r", expected_publication_sha256=draft["publication_sha256"]
    )


def test_full_local_lifecycle_is_idempotent_not_sent(publication_setup):
    s = publication_setup
    draft = propose(s)
    assert draft["status"] == "pending_approval"
    assert draft["content"] == CONTENT
    assert propose(s) == draft
    with pytest.raises(SceneError) as err:
        approve(s, actor=1)
    assert err.value.status == 403
    result = approve(s)
    assert result["status"] == "blocked_not_connected"
    assert result["approved_by"] == 2
    assert (
        not result["dispatch_enabled"]
        and not result["connected"]
        and not result["execution_authorized"]
    )
    assert approve(s) == result
    assert publication_service(s, 2).get("o", "r") == result
    result = publication_service(s).revoke(
        "o", "r", expected_publication_sha256=draft["publication_sha256"]
    )
    assert result["status"] == "revoked"
    assert (
        publication_service(s).revoke(
            "o", "r", expected_publication_sha256=draft["publication_sha256"]
        )
        == result
    )
    with pytest.raises(SceneError):
        propose(s)
    with s[0].connect() as c:
        logs = c.execute(
            "SELECT payload FROM audit_log WHERE action LIKE 'scene.mattermost_publication.%'"
        ).fetchall()
        assert len(logs) == 3
        assert all(CONTENT not in row[0] and "credential_ref" not in row[0] for row in logs)
        assert (
            CONTENT
            not in c.execute("SELECT snapshot_json FROM scene_mattermost_publications").fetchone()[
                0
            ]
        )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE users SET disabled=1 WHERE id=2",
        "UPDATE users SET disabled=1 WHERE id=1",
        "DELETE FROM organization_members WHERE user_id=1",
        "UPDATE organization_members SET role='member' WHERE user_id=1",
        "UPDATE organization_members SET role='member' WHERE user_id=2",
        "DELETE FROM organization_members WHERE user_id=2",
        "UPDATE agents SET is_shared=0",
        "UPDATE agents SET enabled=0",
        "UPDATE users SET permissions='[]' WHERE id=2",
        "UPDATE settings SET value='{}' WHERE key='enterprise_assistant.features.v1'",
        "UPDATE digital_workers SET version=version+1",
        "UPDATE digital_workers SET status='paused'",
        "DELETE FROM organization_assignments",
        "UPDATE worker_mattermost_intents SET revision=revision+1",
        "UPDATE worker_mattermost_intents SET status='revoked'",
        "UPDATE worker_mattermost_intents SET channel_id='different'",
        "UPDATE mattermost_instances SET revision=revision+1",
        "UPDATE mattermost_instances SET status='revoked'",
        "UPDATE mattermost_instances SET origin='https://1.1.1.1'",
        "UPDATE scene_inspection_batches SET content='tampered'",
        "UPDATE scene_inspection_batches SET content_sha256='bad'",
        "UPDATE enterprise_scene_runs SET inspection_report_sha256='bad'",
        "UPDATE scene_run_acceptances SET decision='changes_requested'",
        "UPDATE enterprise_scene_runs SET updated_at=2",
        "UPDATE enterprise_scene_runs SET report_attachment_sha256='other-report'",
    ],
)
def test_changed_authority_or_binding_blocks_approval_and_read(publication_setup, sql):
    s = publication_setup
    draft = propose(s)
    with s[0].transaction() as c:
        c.execute(sql)
    for method in (
        lambda: publication_service(s, 2).approve(
            "o", "r", expected_publication_sha256=draft["publication_sha256"]
        ),
        lambda: publication_service(s, 2).get("o", "r"),
        lambda: publication_service(s).candidate("o", "r"),
    ):
        with pytest.raises(SceneError) as err:
            method()
        assert err.value.status in {403, 409}
    with s[0].connect() as c:
        assert (
            c.execute("SELECT status FROM scene_mattermost_publications").fetchone()[0]
            == "pending_approval"
        )


def test_wrong_hash_org_actor_and_stale_versions(publication_setup):
    s = publication_setup
    for key in preview_args():
        wrong = preview_args() | {key: "a" * 64 if "sha256" in key else 2}
        with pytest.raises(SceneError) as err:
            publication_service(s).preview("o", "r", **wrong)
        assert err.value.status == 409
    with pytest.raises(SceneError) as err:
        publication_service(s).preview("other", "r", **preview_args())
    assert err.value.status == 404
    with pytest.raises(SceneError) as err:
        publication_service(s, 2).preview("o", "r", **preview_args())
    assert err.value.status == 403
    with pytest.raises(SceneError) as err:
        publication_service(s, 999).get("o", "r")
    assert err.value.status == 403
    propose(s)
    with pytest.raises(SceneError) as err:
        publication_service(s, 2).approve("o", "r", expected_publication_sha256="a" * 64)
    assert err.value.status == 409


@pytest.mark.parametrize("action", ["preview", "approve", "revoke"])
def test_audit_failure_rolls_back_and_is_sanitized(publication_setup, action):
    s = publication_setup
    draft = propose(s) if action != "preview" else None
    with s[0].connect() as c:
        c.executescript(
            "CREATE TRIGGER fail_publication_audit BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT,'PRIVATE_SQL_SENTINEL'); END;"
        )
    with pytest.raises(SceneError) as err:
        if action == "preview":
            propose(s)
        else:
            getattr(publication_service(s, 2), action)(
                "o", "r", expected_publication_sha256=draft["publication_sha256"]
            )
    assert err.value.status == 503
    assert "PRIVATE_SQL_SENTINEL" not in str(err.value)
    with s[0].connect() as c:
        rows = c.execute("SELECT status FROM scene_mattermost_publications").fetchall()
        assert ([r[0] for r in rows]) == ([] if action == "preview" else ["pending_approval"])


def test_unknown_cannot_be_retried_or_claimed_sent(publication_setup):
    s = publication_setup
    draft = propose(s)
    with s[0].transaction() as c:
        c.execute("UPDATE scene_mattermost_publications SET status='unknown'")
    assert publication_service(s, 2).get("o", "r")["status"] == "unknown"
    for action in ("approve", "revoke"):
        with pytest.raises(SceneError) as err:
            getattr(publication_service(s, 2), action)(
                "o", "r", expected_publication_sha256=draft["publication_sha256"]
            )
        assert err.value.status == 409
    with pytest.raises(SceneError):
        propose(s)
    with pytest.raises(sqlite3.IntegrityError), s[0].transaction() as c:
        c.execute("UPDATE scene_mattermost_publications SET status='sent'")


def test_concurrent_duplicate_approvals_one_audit(publication_setup):
    s = publication_setup
    draft = propose(s)

    def decision(_):
        return publication_service(s, 2).approve(
            "o", "r", expected_publication_sha256=draft["publication_sha256"]
        )

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(decision, range(2)))
    assert results[0] == results[1]
    with s[0].connect() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM audit_log WHERE action='scene.mattermost_publication.approved_locally'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE users SET disabled=1 WHERE id=3",
        "UPDATE users SET role='user' WHERE id=3",
    ],
)
def test_publication_admin_approver_revocation_invalidates_existing_approval(
    publication_setup, sql
):
    s = publication_setup
    approved = approve(s, actor=3)
    with s[0].transaction() as c:
        c.execute(sql)
    with pytest.raises(SceneError) as err:
        publication_service(s).get("o", "r")
    assert err.value.status == 403
    with pytest.raises(SceneError) as err:
        propose(s)
    assert err.value.status == 403
    revoked = publication_service(s).revoke(
        "o", "r", expected_publication_sha256=approved["publication_sha256"]
    )
    assert revoked["status"] == "revoked" and revoked["content"] == ""


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE mattermost_instances SET status='revoked'",
        "UPDATE scene_inspection_batches SET content='changed'",
        "UPDATE users SET disabled=1 WHERE id=1",
    ],
)
def test_revoke_stale_publication_without_exposing_changed_report(publication_setup, sql):
    s = publication_setup
    draft = propose(s)
    with s[0].transaction() as c:
        c.execute(sql)
    revoked = publication_service(s, 2).revoke(
        "o", "r", expected_publication_sha256=draft["publication_sha256"]
    )
    assert revoked["status"] == "revoked" and revoked["content"] == ""


def test_two_sqlite_connections_competing_reviewers_have_one_winner(publication_setup):
    from octop.infra.db.pool import SqlitePool
    from octop.infra.db.services import RepoBundle

    s = publication_setup
    draft = propose(s)
    other = SqlitePool(s[0].path)
    services = [
        publication_service(s, 2),
        MattermostPublicationService(RepoBundle.from_pool(other), 3),
    ]

    def decision(index):
        try:
            return services[index].approve(
                "o", "r", expected_publication_sha256=draft["publication_sha256"]
            )["approved_by"]
        except SceneError as exc:
            return exc.status

    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(decision, (0, 1)))
        assert results.count(409) == 1
        assert sum(result in (2, 3) for result in results) == 1
        with s[0].connect() as c:
            assert (
                c.execute(
                    "SELECT count(*) FROM audit_log WHERE action='scene.mattermost_publication.approved_locally'"
                ).fetchone()[0]
                == 1
            )
    finally:
        other.close()


def test_real_v40_upgrade_and_repeat_preserve_state(tmp_path, monkeypatch):
    from octop.infra.db import migrate
    from octop.infra.db.pool import SqlitePool

    db = SqlitePool(tmp_path / "upgrade-publication.db")
    discover = migrate._discover
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                migrate,
                "_discover",
                lambda dialect="sqlite": [(n, p) for n, p in discover(dialect) if n <= 40],
            )
            migrate.run_migrations(db)
        with db.transaction() as c:
            assert c.execute("SELECT version FROM _schema_version").fetchone()[0] == 40
            c.execute("INSERT INTO settings(key,value) VALUES('publication-upgrade','preserve')")
        migrate.run_migrations(db)
        migrate.run_migrations(db)
        with db.connect() as c:
            assert (
                c.execute("SELECT version FROM _schema_version").fetchone()[0]
                == migrate.schema_baseline_version()
            )
            assert (
                c.execute("SELECT value FROM settings WHERE key='publication-upgrade'").fetchone()[
                    0
                ]
                == "preserve"
            )
            assert (
                c.execute("SELECT count(*) FROM scene_mattermost_publications").fetchone()[0] == 0
            )
            assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            dict(discover("sqlite"))[41].read_text() == dict(discover("postgresql"))[41].read_text()
        )
    finally:
        db.close()
