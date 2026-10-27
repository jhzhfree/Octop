"""Unit tests for external knowledge sources (repo, service, migration 028)."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import DatabasePool, SqlitePool
from octop.infra.db.repos.connectors import ConnectorRepo
from octop.infra.db.repos.external_knowledge import ExternalKnowledgeRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.knowledge.external.service import ExternalKnowledgeService
from octop.infra.knowledge.external.types import ExternalSourceError


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def owner_id(db: DatabasePool) -> int:
    return UserRepo(db).create(username="owner", password_hash="h", role="user")


@pytest.fixture
def service(db: DatabasePool) -> ExternalKnowledgeService:
    return ExternalKnowledgeService(ExternalKnowledgeRepo(db), ConnectorRepo(db), SecretRepo(db))


def test_migration_creates_tables(db: DatabasePool) -> None:
    with db.connect() as conn:
        names = {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    assert {"external_knowledge_sources", "external_knowledge_bindings"}.issubset(names)
    assert version == 28


def test_create_list_and_delete_source(service: ExternalKnowledgeService, owner_id: int) -> None:
    created = service.create_source(
        user_id=owner_id,
        kind="akb-mcp",
        display_name="AKB HR",
        endpoint="https://akb.example.com",
        api_key="secret-key",
        ref="HR-POLICY",
    )
    assert created["has_credentials"] is True
    assert "api_key" not in created
    assert created["kind"] == "akb-mcp"

    rows = service.list_sources(user_id=owner_id, is_admin=False)
    assert [row["source_id"] for row in rows] == [created["source_id"]]

    service.delete_source(created["source_id"], user_id=owner_id, is_admin=False)
    assert service.list_sources(user_id=owner_id, is_admin=False) == []


def test_credentials_are_encrypted_at_rest(
    service: ExternalKnowledgeService, owner_id: int, db: DatabasePool
) -> None:
    created = service.create_source(
        user_id=owner_id,
        kind="akb-rest",
        display_name="AKB REST",
        endpoint="https://akb.example.com",
        api_key="top-secret",
        ref="KB1",
    )
    row = ExternalKnowledgeRepo(db).get(created["source_id"])
    assert row is not None
    blob = row.credential_blob or b""
    assert b"top-secret" not in blob
    assert service.credentials_for(row)["api_key"] == "top-secret"


def test_create_source_rejects_bad_input(service: ExternalKnowledgeService, owner_id: int) -> None:
    with pytest.raises(ExternalSourceError):
        service.create_source(
            user_id=owner_id,
            kind="nope",
            display_name="x",
            endpoint="https://host",
        )
    with pytest.raises(ExternalSourceError):
        service.create_source(user_id=owner_id, kind="akb-mcp", display_name="x", endpoint="")
    with pytest.raises(ExternalSourceError):
        service.create_source(
            user_id=owner_id,
            kind="akb-mcp",
            display_name="x",
            endpoint="https://host",
            timeout_ms=999_999,
        )


def test_bindings_round_trip(service: ExternalKnowledgeService, owner_id: int) -> None:
    created = service.create_source(
        user_id=owner_id,
        kind="standard-retrieval",
        display_name="Vendor KB",
        endpoint="https://vendor.example.com/retrievals",
        api_key="k",
        ref="kb-1",
    )
    source_id = created["source_id"]
    service.set_binding(
        source_id,
        user_id=owner_id,
        is_admin=False,
        target_type="agent",
        target_id="agent-1",
    )
    service.set_binding(
        source_id,
        user_id=owner_id,
        is_admin=False,
        target_type="knowledge_base",
        target_id="kb-abc",
    )
    assert len(service.list_bindings(source_id)) == 2

    specs = service.specs_for_target("agent", "agent-1")
    assert [spec.binding_id for spec in specs] == [source_id]
    assert specs[0].kind == "standard-retrieval"
    assert specs[0].ref == "kb-1"

    service.delete_binding(
        source_id,
        user_id=owner_id,
        is_admin=False,
        target_type="agent",
        target_id="agent-1",
    )
    assert service.specs_for_target("agent", "agent-1") == []


def test_update_source_toggles_and_patches(
    service: ExternalKnowledgeService, owner_id: int
) -> None:
    created = service.create_source(
        user_id=owner_id,
        kind="akb-mcp",
        display_name="AKB",
        endpoint="https://akb.example.com",
        api_key="k",
    )
    source_id = created["source_id"]
    updated = service.update_source(
        source_id,
        user_id=owner_id,
        is_admin=False,
        display_name="AKB prod",
        enabled=False,
        top_k=3,
        read_tools=["knowledge_search"],
    )
    assert updated["display_name"] == "AKB prod"
    assert updated["enabled"] is False
    assert updated["top_k"] == 3
    assert updated["read_tools"] == ["knowledge_search"]


def test_other_users_cannot_manage(service: ExternalKnowledgeService, owner_id: int) -> None:
    created = service.create_source(
        user_id=owner_id,
        kind="akb-mcp",
        display_name="Private",
        endpoint="https://akb.example.com",
        api_key="k",
    )
    with pytest.raises(ExternalSourceError) as excinfo:
        service.delete_source(created["source_id"], user_id=owner_id + 99, is_admin=False)
    assert excinfo.value.code == "FORBIDDEN"
