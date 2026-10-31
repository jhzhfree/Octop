"""Connection lifecycle, separate from knowledge resources and mounts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cryptography.fernet import InvalidToken

from octop.infra.connectors.crypto import decrypt_credentials, encrypt_credentials
from octop.infra.db.repos._base import now_ts
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.external.http import validate_endpoint
from octop.infra.knowledge.external.types import SOURCE_KINDS, ExternalSourceError
from octop.infra.utils.ulid import new_ulid

if TYPE_CHECKING:
    from octop.infra.knowledge.external.service import ExternalKnowledgeService


class Connections:
    def __init__(self, sources: ExternalKnowledgeService):
        self.sources = sources
        self.repo = sources._repo

    def _public(self, row: dict[str, Any], user_id: int, is_admin: bool) -> dict[str, Any]:
        attention = row["auth_type"] == "legacy"
        try:
            credentials = (
                decrypt_credentials(self.sources._secret_repo, row["credential_blob"])
                if row["credential_blob"]
                else {}
            )
            has_credentials = bool(credentials.get("api_key") or row["instance_id"])
        except (InvalidToken, ValueError):
            has_credentials = False
            attention = True
        return {k: v for k, v in row.items() if k not in {"credential_blob", "identity_json"}} | {
            "has_credentials": has_credentials,
            "needs_attention": attention,
            "enabled": bool(row["enabled"]),
            "shared": bool(row["shared"]),
            "allow_insecure_http": bool(row["allow_insecure_http"]),
            "can_manage": is_admin or row["owner_user_id"] == user_id,
            "resource_count": len(self.repo.connection_resources(row["connection_id"])),
            "agent_count": len(
                self.repo.identity_for_connection(row["connection_id"]).get("agents", {})
            ),
        }

    def list(self, user_id: int, is_admin: bool) -> list[dict[str, Any]]:
        self.sources.list_sources(user_id=user_id, is_admin=is_admin)
        return [
            self._public(row, user_id, is_admin)
            for row in self.repo.list_connections(user_id, is_admin=is_admin)
        ]

    def create(self, user_id: int, **values: Any) -> dict[str, Any]:
        kind = values["kind"]
        if kind not in SOURCE_KINDS or not str(values.get("display_name") or "").strip():
            raise ExternalSourceError(
                "INVALID_ARGUMENT", "connection name and supported product are required"
            )
        mode = self.sources._validate_auth_type(values.get("auth_type", "service"), kind)
        endpoint = validate_endpoint(
            values["endpoint"], allow_insecure_http=values.get("allow_insecure_http", False)
        )
        instance_id = values.get("instance_id", "")
        if instance_id:
            instance = self.sources._connector_repo.get(instance_id)
            if (
                instance is None
                or instance.status != "active"
                or (instance.user_id != user_id and not instance.shared)
            ):
                raise ExternalSourceError("FORBIDDEN", "connector is not available")
        credential = {
            "api_key": values.get("api_key", "").strip(),
            "api_key_header": values.get("api_key_header", ""),
        }
        connection_id = new_ulid()
        self.repo.create_connection(
            {
                "connection_id": connection_id,
                "owner_user_id": user_id,
                "kind": kind,
                "display_name": values["display_name"].strip(),
                "endpoint": endpoint,
                "instance_id": instance_id,
                "auth_type": mode,
                "allow_insecure_http": int(values.get("allow_insecure_http", False)),
                "shared": int(values.get("shared", False)),
                "credential_blob": encrypt_credentials(self.sources._secret_repo, credential),
                "created_at": now_ts(),
                "updated_at": now_ts(),
            }
        )
        row = self.repo.connection(connection_id)
        assert row is not None
        return self._public(row, user_id, False)

    def update(
        self, connection_id: str, user_id: int, is_admin: bool, values: dict[str, Any]
    ) -> dict[str, Any]:
        row = self.repo.connection(connection_id)
        if row is None or (not is_admin and row["owner_user_id"] != user_id):
            raise ExternalSourceError("FORBIDDEN", "connection is not available")
        values = {k: v for k, v in values.items() if v is not None}
        revision = int(values.pop("revision", row["revision"]))
        kind = str(values.get("kind", row["kind"]))
        if kind not in SOURCE_KINDS:
            raise ExternalSourceError("INVALID_ARGUMENT", "unsupported product")
        self.sources._validate_auth_type(str(values.get("auth_type", row["auth_type"])), kind)
        config = self.repo.identity_for_connection(connection_id)
        if config.get("agents") and any(
            k in values and values[k] != row[k] for k in ("kind", "endpoint", "auth_type")
        ):
            raise OctopError.localized(ErrorCode.AGENT_IDENTITY_INVALID)
        if "endpoint" in values:
            values["endpoint"] = validate_endpoint(
                values["endpoint"],
                allow_insecure_http=bool(
                    values.get("allow_insecure_http", row["allow_insecure_http"])
                ),
            )
        if "api_key" in values or "api_key_header" in values:
            try:
                credential = (
                    decrypt_credentials(self.sources._secret_repo, row["credential_blob"])
                    if row["credential_blob"]
                    else {}
                )
            except (InvalidToken, ValueError) as exc:
                if not values.get("api_key") or not values.get("auth_type"):
                    raise ExternalSourceError(
                        "INVALID_CREDENTIALS",
                        "choose authentication type and replace the unreadable credential",
                    ) from exc
                credential = {}
            if values.get("api_key"):
                if row["instance_id"]:
                    raise ExternalSourceError(
                        "INVALID_ARGUMENT",
                        "linked connector credentials must be changed on the connector",
                    )
                credential["api_key"] = values["api_key"].strip()
            if "api_key_header" in values:
                credential["api_key_header"] = values["api_key_header"]
            values.pop("api_key", None)
            values.pop("api_key_header", None)
            values["credential_blob"] = encrypt_credentials(self.sources._secret_repo, credential)
        for name in ("enabled", "shared", "allow_insecure_http"):
            if name in values:
                values[name] = int(values[name])
        if not values or not self.repo.update_connection(connection_id, revision, values):
            raise OctopError.localized(ErrorCode.AGENT_IDENTITY_CONFLICT)
        current = self.repo.connection(connection_id)
        assert current is not None
        return self._public(current, user_id, is_admin)

    def delete(self, connection_id: str, user_id: int, is_admin: bool) -> None:
        row = self.repo.connection(connection_id)
        if row is None or (not is_admin and row["owner_user_id"] != user_id):
            raise ExternalSourceError("FORBIDDEN", "connection is not available")
        refs = [
            str(agent.get("keyReference") or "")
            for agent in self.repo.identity_for_connection(connection_id).get("agents", {}).values()
        ]
        if not self.repo.delete_connection(connection_id):
            raise OctopError.localized(ErrorCode.AGENT_IDENTITY_CONFLICT)

        from octop.infra.knowledge.external.native_identity import KeyStore, retire_reference

        for reference in refs:
            if reference and not self.repo.key_reference_used(reference):
                retire_reference(KeyStore(self.sources._secret_repo, ""), reference, self.repo)
