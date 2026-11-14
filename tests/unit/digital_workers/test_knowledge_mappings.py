from __future__ import annotations

import json
from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations, schema_baseline_version
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import RepoBundle
from octop.infra.digital_workers.knowledge_mappings import WorkerKnowledgeMappingService
from octop.infra.digital_workers.service import DigitalWorkerError
from octop.infra.users.identity import User


@pytest.fixture
def setup(tmp_path: Path):
    db = SqlitePool(tmp_path / "mapping.db")
    run_migrations(db)
    repos = RepoBundle.from_pool(db)
    with db.transaction() as c:
        c.execute(
            "INSERT INTO users(username,password_hash,role,created_at) VALUES('manager','x','user',1)"
        )
        c.execute(
            "INSERT INTO users(username,password_hash,role,created_at) VALUES('other','x','user',1)"
        )
        c.execute(
            "INSERT INTO agents(agent_id,name,user_id,created_at,updated_at) VALUES('a','Agent',1,1,1)"
        )
        for org in ("o", "other"):
            c.execute(
                "INSERT INTO organizations(organization_id,owner_user_id,name,created_at,updated_at) VALUES(?,1,?,1,1)",
                (org, org),
            )
            c.execute(
                "INSERT INTO organization_members(organization_id,user_id,role,created_at) VALUES(?,1,'owner',1)",
                (org,),
            )
        c.execute(
            "INSERT INTO organization_members(organization_id,user_id,role,created_at) VALUES('o',2,'member',1)"
        )
        c.execute(
            "INSERT INTO organization_positions(position_id,organization_id,title,created_at,updated_at) VALUES('p','o','Analyst',1,1)"
        )
        c.execute(
            "INSERT INTO organization_assignments(assignment_id,position_id,subject_type,subject_id,created_at) VALUES('assignment','p','agent','a',1)"
        )
        c.execute(
            "INSERT INTO enterprise_scenes(scene_id,organization_id,position_id,agent_id,owner_user_id,name,prompt,mcp_servers_json,created_at,updated_at) VALUES('s','o','p','a',1,'Scene','Prompt','[\"akb\"]',1,1)"
        )
        c.execute(
            "INSERT INTO digital_workers(worker_id,organization_id,position_id,agent_id,owner_user_id,name,status,capabilities_json,created_at,updated_at) VALUES('w','o','p','a',1,'Worker','active',?,1,1)",
            (
                json.dumps(
                    {
                        "knowledge_sources": [
                            {"provider": "akb", "mcp_server": "akb", "knowledge_base_ref": "kb"}
                        ]
                    }
                ),
            ),
        )
    repos.external_knowledge_repo.create(
        source_id="e",
        owner_user_id=1,
        kind="akb-mcp",
        display_name="KB",
        endpoint="https://example.invalid",
        ref="kb",
        auth_type="agent",
    )
    repos.external_knowledge_repo.set_binding(
        source_id="e", target_type="agent", target_id="a", enabled=True
    )
    yield db, repos, WorkerKnowledgeMappingService(repos, User(1, "manager", "user", None))
    db.close()


def create(service, **overrides):
    args = {
        "mcp_server": "akb",
        "knowledge_base_ref": "kb",
        "external_source_id": "e",
        "expected_capability_version": 1,
        "expected_connection_revision": 1,
        "expected_resource_revision": 1,
    }
    args.update(overrides)
    return service.create_draft("o", "w", **args)


def test_persists_draft_and_migrations_idempotent(setup):
    db, repos, service = setup
    result = create(service)
    run_migrations(db)
    assert schema_baseline_version() == schema_baseline_version("postgresql")
    record = WorkerKnowledgeMappingService(RepoBundle.from_pool(db), service.actor).inspect(
        "o", "w", result["mapping_id"]
    )
    assert record["local_snapshot_current"]
    assert record["status"] == "draft"
    assert record["retrieval_enabled"] is False
    assert record["authorization_status"] == "delegated_user_acl_unverified"
    with pytest.raises(DigitalWorkerError):
        create(service)
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM worker_knowledge_source_mappings").fetchone()[0] == 1


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE external_knowledge_sources SET resource_revision=2",
        "UPDATE external_knowledge_sources SET ref='other'",
        "UPDATE external_knowledge_connections SET revision=2",
        "UPDATE external_knowledge_connections SET enabled=0",
        "UPDATE external_knowledge_connections SET auth_type='service'",
        "UPDATE external_knowledge_connections SET owner_user_id=2,shared=0",
        "UPDATE external_knowledge_sources SET owner_user_id=2,shared=0",
        "UPDATE digital_workers SET capability_version=2",
        "UPDATE digital_workers SET status='paused'",
        "DELETE FROM organization_assignments",
        "UPDATE enterprise_scenes SET enabled=0",
        "DELETE FROM external_knowledge_bindings",
    ],
)
def test_binding_changes_invalidate_without_authorizing(setup, change):
    db, repos, service = setup
    result = create(service)
    with db.transaction() as c:
        c.execute(change)
    inspected = service.inspect("o", "w", result["mapping_id"])
    assert not inspected["local_snapshot_current"]
    assert not inspected["retrieval_enabled"]


def test_member_and_cross_org_rejected(setup):
    db, repos, service = setup
    with pytest.raises(DigitalWorkerError):
        create(WorkerKnowledgeMappingService(repos, User(2, "other", "user", None)))
    row = create(service)
    with pytest.raises(DigitalWorkerError):
        service.inspect("other", "w", row["mapping_id"])
    with pytest.raises(DigitalWorkerError):
        service.revoke("other", "w", row["mapping_id"], expected_revision=1)


def test_stale_revision_and_revoke_cas(setup):
    db, repos, service = setup
    with pytest.raises(DigitalWorkerError):
        create(service, expected_connection_revision=2)
    row = create(service)
    with pytest.raises(DigitalWorkerError):
        service.revoke("o", "w", row["mapping_id"], expected_revision=9)
    revoked = service.revoke("o", "w", row["mapping_id"], expected_revision=1)
    assert revoked["status"] == "revoked" and revoked["revision"] == 2
    assert not revoked["local_snapshot_current"] and not revoked["retrieval_enabled"]
    with pytest.raises(DigitalWorkerError):
        service.revoke("o", "w", row["mapping_id"], expected_revision=1)


def test_insert_rechecks_versions_and_does_not_guess_source(setup):
    db, repos, service = setup
    with pytest.raises(DigitalWorkerError):
        create(service, external_source_id="same-name-is-not-id")
    values = service._snapshot(
        "o", "w", mcp_server="akb", knowledge_base_ref="kb", external_source_id="e"
    )
    with db.transaction() as c:
        c.execute("UPDATE external_knowledge_sources SET resource_revision=2")
    assert repos.worker_knowledge_mapping_repo.create_draft(values, 1) is None


def test_revoke_preserves_history_and_requires_revalidation_for_new_draft(setup):
    db, repos, service = setup
    original = create(service)
    service.revoke("o", "w", original["mapping_id"], expected_revision=1)
    with db.transaction() as c:
        c.execute("UPDATE external_knowledge_connections SET enabled=0")
    with pytest.raises(DigitalWorkerError):
        create(service)
    with db.transaction() as c:
        c.execute("UPDATE external_knowledge_connections SET enabled=1,revision=2")
    with pytest.raises(DigitalWorkerError):
        create(service)
    new = create(service, expected_connection_revision=2)
    assert new["mapping_id"] != original["mapping_id"]
    assert not new["retrieval_enabled"]
    assert service.inspect("o", "w", original["mapping_id"])["status"] == "revoked"
