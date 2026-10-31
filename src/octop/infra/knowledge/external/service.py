"""Management facade for external knowledge sources.

Owns source configuration, credential decryption, capability probing and
multi-source retrieval. Callers never see raw credentials.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from cryptography.fernet import InvalidToken

from octop.infra.connectors.crypto import decrypt_credentials, encrypt_credentials
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.connectors import ConnectorRepo
from octop.infra.db.repos.external_knowledge import (
    TARGET_TYPES,
    ExternalKnowledgeRepo,
    ExternalSourceRow,
    is_reference_conflict,
)
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.knowledge.akb_identity import OctopIdentity
from octop.infra.knowledge.external.fuse import rrf_fuse
from octop.infra.knowledge.external.registry import get_adapter, supported_kinds
from octop.infra.knowledge.external.types import (
    API_KEY_HEADERS,
    DEFAULT_TOP_K,
    MAX_TIMEOUT_SECONDS,
    MAX_TOP_K,
    SOURCE_KINDS,
    ExternalChunk,
    ExternalSearchResult,
    ExternalSourceError,
    ExternalSourceSpec,
    SourceWarning,
    sanitize_tool_arguments,
)
from octop.infra.utils.ulid import new_ulid

logger = logging.getLogger(__name__)


def _split_list(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in (raw or "").split(",") if item.strip())


def _join_list(items: Any) -> str:
    if isinstance(items, (list, tuple)):
        return ",".join(str(item).strip() for item in items if str(item).strip())
    return str(items or "")


def _normalize_api_key_header(value: str) -> str:
    """Keep only a known header scheme so an arbitrary string cannot leak in."""
    scheme = (value or "").strip().lower()
    return scheme if scheme in API_KEY_HEADERS else ""


def _load_tool_arguments(raw: str) -> dict[str, Any]:
    """Read stored tool arguments, tolerating a hand-edited or empty column."""
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        return {}
    return sanitize_tool_arguments(parsed)


def _dump_tool_arguments(values: Any) -> str:
    clean = sanitize_tool_arguments(values)
    return json.dumps(clean, ensure_ascii=False, sort_keys=True) if clean else ""


class ExternalKnowledgeService:
    def __init__(
        self,
        repo: ExternalKnowledgeRepo,
        connector_repo: ConnectorRepo,
        secret_repo: SecretRepo,
    ) -> None:
        self._repo = repo
        self._connector_repo = connector_repo
        self._secret_repo = secret_repo

    # ---------------------------------------------------------------- config

    @staticmethod
    def supported_kinds() -> list[str]:
        return supported_kinds()

    def to_spec(self, row: ExternalSourceRow) -> ExternalSourceSpec:
        return ExternalSourceSpec(
            binding_id=row.source_id,
            display_name=row.display_name,
            kind=row.kind,
            endpoint=row.endpoint,
            ref=row.ref,
            top_k=max(1, min(row.top_k, MAX_TOP_K)),
            min_score=max(0.0, min(row.min_score, 1.0)),
            timeout_seconds=min(row.timeout_ms / 1000.0, MAX_TIMEOUT_SECONDS),
            allow_insecure_http=row.allow_insecure_http,
            selector_argument=row.selector_argument,
            forbidden_arguments=_split_list(row.forbidden_arguments),
            read_tools=_split_list(row.read_tools),
            tool_name=(row.tool_name or "").strip(),
            tool_arguments=_load_tool_arguments(row.tool_arguments),
            enabled=row.enabled,
            instance_id=row.instance_id,
            owner_user_id=row.owner_user_id,
            shared=row.shared,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def credentials_for(self, row: ExternalSourceRow) -> dict[str, Any]:
        """Decrypt credentials, preferring a linked connector instance."""
        row = self._migrate_row(row)
        blob = row.credential_blob
        if row.instance_id:
            instance = self._connector_repo.get(row.instance_id)
            if (
                instance is None
                or instance.status != "active"
                or (instance.user_id != row.connection_owner_user_id and not instance.shared)
            ):
                raise ExternalSourceError(
                    "FORBIDDEN", "connector is not available", origin=row.kind
                )
            blob = instance.credential_blob
        if not blob:
            raise ExternalSourceError(
                "INVALID_ARGUMENT", "credentials are missing", origin=row.kind
            )
        try:
            result = decrypt_credentials(self._secret_repo, blob)
            if row.instance_id and row.credential_blob:
                own = decrypt_credentials(self._secret_repo, row.credential_blob)
                for field in ("agent_identity", "auth_type"):
                    if field in own:
                        result[field] = own[field]
            result["agent_identity"] = self.identity_config(row)
            result["auth_type"] = self.auth_type(row)
            result["_secret_repo"] = self._secret_repo
            return result
        except Exception as exc:  # noqa: BLE001 - never leak key material
            logger.warning("external knowledge credentials unreadable for source=%s", row.source_id)
            raise ExternalSourceError(
                "INVALID_CREDENTIALS", "stored credentials could not be read", origin=row.kind
            ) from exc

    def _migrate_row(self, row: ExternalSourceRow) -> ExternalSourceRow:
        if row.auth_type != "legacy":
            return row
        own = (
            decrypt_credentials(self._secret_repo, row.credential_blob)
            if row.credential_blob
            else {}
        )
        config = own.pop("agent_identity", {})
        explicit_mode = own.pop("auth_type", None)
        if explicit_mode is None and config and not config.get("simple"):
            raise ExternalSourceError(
                "INVALID_CREDENTIALS",
                "legacy identity configuration requires an explicit authentication choice",
            )
        mode = explicit_mode or ("agent" if config.get("simple") else "service")
        self._validate_auth_type(mode, row.kind)
        self._repo.import_identity(row, mode, encrypt_credentials(self._secret_repo, own), config)
        return self._repo.get(row.source_id) or row

    def auth_type(self, row: ExternalSourceRow) -> str:
        row = self._migrate_row(row)
        return self._validate_auth_type(row.auth_type, row.kind)

    @staticmethod
    def _validate_auth_type(mode: str, kind: str) -> str:
        if mode not in {"service", "agent"} or (
            mode == "agent" and kind not in {"akb-mcp", "akb-rest"}
        ):
            from octop.infra.errors import ErrorCode, OctopError

            raise OctopError.localized(ErrorCode.AGENT_IDENTITY_INVALID)
        return mode

    def list_sources(self, *, user_id: int, is_admin: bool) -> list[dict[str, Any]]:
        rows = self._repo.list_visible(user_id, is_admin=is_admin)
        result: list[dict[str, Any]] = []
        bindings: dict[str, list[dict[str, str]]] = {}
        for binding in self._repo.list_bindings():
            bindings.setdefault(binding.source_id, []).append(
                {"target_type": binding.target_type, "target_id": binding.target_id}
            )
        for row in rows:
            try:
                row = self._migrate_row(row)
                public = self._public_source(row, self.to_spec(row))
            except (ExternalSourceError, InvalidToken, ValueError):
                public = self._public_source(row, self.to_spec(row), unavailable=True)
            result.append(
                {
                    **public,
                    "can_manage": is_admin or row.owner_user_id == user_id,
                    "can_manage_connection": is_admin or row.connection_owner_user_id == user_id,
                    "bindings": bindings.get(row.source_id, []),
                }
            )
        return result

    def _public_source(
        self, row: ExternalSourceRow, spec: ExternalSourceSpec, *, unavailable: bool = False
    ) -> dict[str, Any]:
        """Public projection: no credentials, no key material."""
        identity_ready = False
        api_key_header = API_KEY_HEADERS[0]
        config: dict[str, Any] = {}
        try:
            if unavailable:
                raise ExternalSourceError("INVALID_CREDENTIALS", "configuration requires attention")
            credentials = self.credentials_for(row)
            has_credentials = bool(credentials.get("api_key"))
            config = credentials.get("agent_identity", {})
            identity_ready = self.auth_type(row) == "agent" and any(
                check["agent_id"] and check["status"] == "success" and check["stage"] == "identity"
                for check in self._repo.checks(row.connection_id, row.source_id)
            )
            stored = str(credentials.get("api_key_header") or "")
            if stored in API_KEY_HEADERS:
                api_key_header = stored
        except ExternalSourceError:
            has_credentials = False
        return {
            "auth_type": self.auth_type(row) if not unavailable else None,
            "needs_attention": unavailable
            or (
                row.kind == "akb-mcp"
                and bool(row.tool_name)
                and row.tool_name not in {"knowledge_search", "knowledge_query"}
            ),
            "connection_enabled": row.connection_enabled,
            "connection_id": row.connection_id,
            "revision": row.revision,
            "resource_revision": row.resource_revision,
            "checks": self._repo.checks(row.connection_id, row.source_id),
            "identity_ready": identity_ready,
            "identity_configured": bool(config.get("agents")) if has_credentials else False,
            "runtime_ready": True,
            "api_key_header": api_key_header,
            "source_id": row.source_id,
            "display_name": row.display_name,
            "kind": row.kind,
            "endpoint": row.endpoint,
            "ref": row.ref,
            "top_k": spec.top_k,
            "min_score": spec.min_score,
            "timeout_ms": row.timeout_ms,
            "allow_insecure_http": row.allow_insecure_http,
            "selector_argument": row.selector_argument,
            "forbidden_arguments": list(spec.forbidden_arguments),
            "read_tools": list(spec.read_tools),
            "tool_name": spec.tool_name,
            "tool_arguments": spec.tool_arguments,
            "enabled": row.enabled,
            "shared": row.shared,
            "owner_user_id": row.owner_user_id,
            "instance_id": row.instance_id,
            "has_credentials": has_credentials,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def identity_config(self, row: ExternalSourceRow) -> dict[str, Any]:
        row = self._migrate_row(row)
        return self._repo.identity(row)

    def save_identity_config(
        self,
        row: ExternalSourceRow,
        config: dict[str, Any],
        *,
        agent_id: str | None = None,
        bound: bool = True,
    ) -> None:
        row = self._migrate_row(row)
        try:
            saved = self._repo.save_identity(row, config, agent_id, bound=bound)
        except Exception as exc:
            if not is_reference_conflict(exc):
                raise
            saved = False
        if not saved:
            from octop.infra.errors import ErrorCode, OctopError

            raise OctopError.localized(ErrorCode.AGENT_IDENTITY_CONFLICT)

    def create_source(
        self,
        *,
        user_id: int,
        kind: str,
        display_name: str,
        endpoint: str,
        api_key: str = "",
        api_key_header: str = "",
        auth_type: str = "service",
        connection_id: str = "",
        instance_id: str = "",
        ref: str = "",
        top_k: int = DEFAULT_TOP_K,
        min_score: float = 0.0,
        timeout_ms: int = 10_000,
        allow_insecure_http: bool = False,
        selector_argument: str = "",
        forbidden_arguments: Any = None,
        read_tools: Any = None,
        tool_name: str = "",
        tool_arguments: Any = None,
        shared: bool = False,
    ) -> dict[str, Any]:
        if connection_id:
            connection = self._repo.connection(connection_id)
            if (
                connection is None
                or not connection["enabled"]
                or (connection["owner_user_id"] != user_id and not connection["shared"])
            ):
                raise ExternalSourceError("FORBIDDEN", "connection is not available")
            kind = str(connection["kind"])
            endpoint = str(connection["endpoint"])
            instance_id = str(connection["instance_id"])
            auth_type = str(connection["auth_type"])
            allow_insecure_http = bool(connection["allow_insecure_http"])
            if api_key:
                raise ExternalSourceError(
                    "INVALID_ARGUMENT",
                    "reused connection credentials must be managed on the connection",
                )
        if kind not in SOURCE_KINDS:
            raise ExternalSourceError("INVALID_ARGUMENT", f"unsupported kind: {kind}")
        if (
            kind == "akb-mcp"
            and tool_name
            and tool_name not in {"knowledge_search", "knowledge_query"}
        ):
            raise ExternalSourceError("INVALID_ARGUMENT", "unsupported AKB retrieval tool")
        name = (display_name or "").strip()
        if not name:
            raise ExternalSourceError("INVALID_ARGUMENT", "display_name is required")
        from octop.infra.knowledge.external.http import validate_endpoint

        endpoint = validate_endpoint(endpoint, allow_insecure_http=allow_insecure_http)
        if not (endpoint or "").strip():
            raise ExternalSourceError("INVALID_ARGUMENT", "endpoint is required")
        if timeout_ms <= 0 or timeout_ms > int(MAX_TIMEOUT_SECONDS * 1000):
            raise ExternalSourceError("INVALID_ARGUMENT", "timeout_ms must be between 1 and 60000")
        if top_k < 1 or top_k > MAX_TOP_K:
            raise ExternalSourceError("INVALID_ARGUMENT", f"top_k must be 1-{MAX_TOP_K}")
        if not 0.0 <= min_score <= 1.0:
            raise ExternalSourceError("INVALID_ARGUMENT", "min_score must be within [0, 1]")
        self._validate_auth_type(auth_type, kind)
        payload: dict[str, Any] = {}
        blob: bytes | None = None
        if instance_id and not connection_id:
            instance = self._connector_repo.get(instance_id)
            if (
                instance is None
                or instance.status != "active"
                or (instance.user_id != user_id and not instance.shared)
            ):
                raise ExternalSourceError("FORBIDDEN", "connector is not available")
        elif api_key.strip():
            payload["api_key"] = api_key.strip()
            scheme = _normalize_api_key_header(api_key_header)
            if scheme:
                payload["api_key_header"] = scheme
        blob = encrypt_credentials(self._secret_repo, payload)
        source_id = new_ulid()
        self._repo.create(
            source_id=source_id,
            owner_user_id=user_id,
            kind=kind,
            display_name=name,
            endpoint=endpoint.strip(),
            instance_id=instance_id,
            ref=(ref or "").strip(),
            top_k=top_k,
            min_score=min_score,
            timeout_ms=timeout_ms,
            allow_insecure_http=allow_insecure_http,
            selector_argument=selector_argument.strip(),
            forbidden_arguments=_join_list(forbidden_arguments),
            read_tools=_join_list(read_tools),
            tool_name=(tool_name or "").strip(),
            tool_arguments=_dump_tool_arguments(tool_arguments),
            shared=shared,
            credential_blob=blob,
            auth_type=auth_type,
            connection_id=connection_id,
        )
        row = self._require_get(source_id)
        return {
            **self._public_source(row, self.to_spec(row)),
            "can_manage": True,
            "can_manage_connection": row.connection_owner_user_id == user_id,
        }

    def update_source(
        self,
        source_id: str,
        *,
        user_id: int,
        is_admin: bool,
        **fields: Any,
    ) -> dict[str, Any]:
        row = self._require_manage(source_id, user_id=user_id, is_admin=is_admin)
        resource_revision = fields.pop("resource_revision", None)
        if resource_revision is not None and resource_revision != row.resource_revision:
            from octop.infra.errors import ErrorCode, OctopError

            raise OctopError.localized(ErrorCode.AGENT_IDENTITY_CONFLICT)
        revision = fields.pop("revision", None)
        if revision is not None and revision != row.revision:
            from octop.infra.errors import ErrorCode, OctopError

            raise OctopError.localized(ErrorCode.AGENT_IDENTITY_CONFLICT)
        if (
            not is_admin
            and row.connection_owner_user_id != user_id
            and any(
                k in fields
                for k in (
                    "kind",
                    "endpoint",
                    "api_key",
                    "api_key_header",
                    "auth_type",
                    "allow_insecure_http",
                    "shared",
                )
            )
        ):
            raise ExternalSourceError("FORBIDDEN", "connection management permission is required")
        if self.identity_config(row).get("agents") and (
            (fields.get("kind") is not None and fields["kind"] != row.kind)
            or (
                fields.get("endpoint") is not None
                and fields["endpoint"].rstrip("/") != row.endpoint.rstrip("/")
            )
        ):
            from octop.infra.errors import ErrorCode, OctopError

            raise OctopError.localized(ErrorCode.AGENT_IDENTITY_INVALID)
        auth_type = fields.pop("auth_type", None)
        mode = self._validate_auth_type(
            auth_type if auth_type is not None else self.auth_type(row),
            fields.get("kind") or row.kind,
        )
        if mode != self.auth_type(row) and self.identity_config(row).get("agents"):
            from octop.infra.errors import ErrorCode, OctopError

            raise OctopError.localized(ErrorCode.AGENT_IDENTITY_INVALID)
        api_key = fields.pop("api_key", None)
        api_key_header = fields.pop("api_key_header", None)
        kind = fields.pop("kind", None)
        forbidden = fields.pop("forbidden_arguments", None)
        read_tools = fields.pop("read_tools", None)
        tool_name = fields.pop("tool_name", None)
        if (
            (kind or row.kind) == "akb-mcp"
            and tool_name
            and tool_name not in {"knowledge_search", "knowledge_query"}
        ):
            raise ExternalSourceError("INVALID_ARGUMENT", "unsupported AKB retrieval tool")
        tool_arguments = fields.pop("tool_arguments", None)
        shared = fields.pop("shared", None)
        enabled = fields.pop("enabled", None)
        allow_insecure = fields.pop("allow_insecure_http", None)
        replace_credentials = False
        # Switching the adapter re-points the source at different upstream
        # semantics, so it is allowed but validated exactly like create.
        if kind is not None and kind not in SOURCE_KINDS:
            raise ExternalSourceError("INVALID_ARGUMENT", f"unsupported kind: {kind}")
        blob: bytes | None = None
        scheme = (
            _normalize_api_key_header(api_key_header) if isinstance(api_key_header, str) else None
        )
        new_key = api_key.strip() if isinstance(api_key, str) else ""
        # The header scheme lives with the key, so it can be changed on its own.
        if ((new_key or scheme is not None) and not row.instance_id) or auth_type is not None:
            previous = (
                decrypt_credentials(self._secret_repo, row.credential_blob)
                if row.credential_blob
                else {}
            )
            merged = {**previous}
            if new_key and not row.instance_id:
                merged["api_key"] = new_key
            if scheme is not None:
                merged["api_key_header"] = scheme
            blob = encrypt_credentials(self._secret_repo, merged)
            replace_credentials = True
        try:
            self._repo.update(
                source_id,
                kind=kind,
                display_name=fields.get("display_name"),
                endpoint=fields.get("endpoint"),
                ref=fields.get("ref"),
                top_k=fields.get("top_k"),
                min_score=fields.get("min_score"),
                timeout_ms=fields.get("timeout_ms"),
                allow_insecure_http=(None if allow_insecure is None else bool(allow_insecure)),
                selector_argument=fields.get("selector_argument"),
                forbidden_arguments=(None if forbidden is None else _join_list(forbidden)),
                read_tools=None if read_tools is None else _join_list(read_tools),
                tool_name=None if tool_name is None else str(tool_name).strip(),
                tool_arguments=None
                if tool_arguments is None
                else _dump_tool_arguments(tool_arguments),
                enabled=None if enabled is None else bool(enabled),
                shared=None if shared is None else bool(shared),
                credential_blob=blob,
                replace_credentials=replace_credentials,
                auth_type=mode if auth_type is not None else None,
                expected_revision=row.revision,
                expected_resource_revision=row.resource_revision,
            )
        except ValueError as exc:
            from octop.infra.errors import ErrorCode, OctopError

            raise OctopError.localized(ErrorCode.AGENT_IDENTITY_CONFLICT) from exc
        updated = self._require_get(source_id)
        return {
            **self._public_source(updated, self.to_spec(updated)),
            "can_manage": True,
            "can_manage_connection": is_admin or updated.connection_owner_user_id == user_id,
        }

    def delete_source(self, source_id: str, *, user_id: int, is_admin: bool) -> None:
        self._require_manage(source_id, user_id=user_id, is_admin=is_admin)
        self._repo.delete(source_id)

    # ------------------------------------------------------------- bindings

    def set_binding(
        self,
        source_id: str,
        *,
        user_id: int,
        is_admin: bool,
        target_type: str,
        target_id: str,
        enabled: bool = True,
    ) -> dict[str, Any]:
        self._require_use(source_id, user_id=user_id, is_admin=is_admin)
        if target_type not in TARGET_TYPES:
            raise ExternalSourceError("INVALID_ARGUMENT", f"unsupported target type: {target_type}")
        target = (target_id or "").strip()
        if not target:
            raise ExternalSourceError("INVALID_ARGUMENT", "target_id is required")
        self._assert_target(target_type, target, user_id=user_id, is_admin=is_admin)
        self._repo.set_binding(
            source_id=source_id,
            target_type=target_type,
            target_id=target,
            enabled=enabled,
        )
        return {
            "source_id": source_id,
            "target_type": target_type,
            "target_id": target,
            "enabled": enabled,
        }

    def delete_binding(
        self,
        source_id: str,
        *,
        user_id: int,
        is_admin: bool,
        target_type: str,
        target_id: str,
    ) -> None:
        self._require_use(source_id, user_id=user_id, is_admin=is_admin)
        self._assert_target(target_type, target_id, user_id=user_id, is_admin=is_admin)
        self._repo.delete_binding(source_id=source_id, target_type=target_type, target_id=target_id)

    def list_bindings(self, source_id: str) -> list[dict[str, Any]]:
        return [
            {
                "source_id": binding.source_id,
                "target_type": binding.target_type,
                "target_id": binding.target_id,
                "enabled": binding.enabled,
            }
            for binding in self._repo.list_bindings(source_id)
        ]

    def specs_for_target(self, target_type: str, target_id: str) -> list[ExternalSourceSpec]:
        """Enabled sources bound to one agent or local knowledge base."""
        specs: list[ExternalSourceSpec] = []
        for source_id in self._repo.list_source_ids_for_target(target_type, target_id):
            row = self._repo.get(source_id)
            if row is None or not row.enabled:
                continue
            specs.append(self.to_spec(row))
        return specs

    def specs_for_targets(
        self, target_type: str, target_ids: list[str]
    ) -> list[ExternalSourceSpec]:
        seen: set[str] = set()
        specs: list[ExternalSourceSpec] = []
        for target_id in target_ids:
            for spec in self.specs_for_target(target_type, target_id):
                if spec.binding_id in seen:
                    continue
                seen.add(spec.binding_id)
                specs.append(spec)
        return specs

    # -------------------------------------------------- probe and retrieval

    async def probe(self, source_id: str, *, user_id: int, is_admin: bool) -> dict[str, Any]:
        row = self._require_manage(source_id, user_id=user_id, is_admin=is_admin)
        if row.kind != "akb-mcp":
            self._require_legacy_management(row)
        adapter = get_adapter(row.kind)
        if adapter is None:
            raise ExternalSourceError(
                "INVALID_ARGUMENT", f"unsupported kind: {row.kind}", origin=row.kind
            )
        spec = self.to_spec(row)
        creds = self.credentials_for(row)
        try:
            async with asyncio.timeout(spec.timeout_seconds):
                result = await adapter.probe(spec, creds)
        except TimeoutError:
            self._repo.record_check(
                row.connection_id, row.source_id, "", "connection", "failed", "TIMEOUT"
            )
            return {"ok": False, "code": "TIMEOUT", "message": "connection deadline exceeded"}
        except ExternalSourceError as exc:
            self._repo.record_check(
                row.connection_id, row.source_id, "", "connection", "failed", exc.code
            )
            return {"ok": False, "code": exc.code, "message": str(exc)}
        except Exception:  # noqa: BLE001 - upstreams are untrusted
            logger.warning("external knowledge probe failed source=%s", source_id, exc_info=True)
            return {"ok": False, "code": "UNEXPECTED", "message": "probe failed"}
        self._repo.record_check(
            row.connection_id, row.source_id, "", "connection", "success" if result.ok else "failed"
        )
        return {
            "auth_type": self.auth_type(row),
            "agent_identity_required": self.auth_type(row) == "agent",
            "ok": result.ok,
            "code": "" if result.ok else "PROBE_FAILED",
            "message": result.message,
            "tools": result.tools,
            "tool_schemas": result.tool_schemas,
            "refs": result.refs,
            "protocol_version": result.protocol_version,
            "server_info": result.server_info,
            "capabilities": result.capabilities,
            "instructions": result.instructions,
        }

    async def list_refs(
        self, source_id: str, *, user_id: int, is_admin: bool
    ) -> list[dict[str, str]]:
        """Knowledge bases the stored credential can read, for ref picklists."""
        row = self._require_manage(source_id, user_id=user_id, is_admin=is_admin)
        self._require_legacy_management(row)
        adapter = get_adapter(row.kind)
        if adapter is None:
            raise ExternalSourceError(
                "INVALID_ARGUMENT", f"unsupported kind: {row.kind}", origin=row.kind
            )
        spec = self.to_spec(row)
        return await adapter.list_refs(spec, self.credentials_for(row))

    def _require_legacy_management(self, row: ExternalSourceRow) -> None:
        if self.auth_type(row) == "agent":
            from octop.infra.errors import ErrorCode, OctopError

            raise OctopError.localized(ErrorCode.AGENT_IDENTITY_NOT_READY)

    async def search_source(
        self,
        source_id: str,
        query: str,
        *,
        user_id: int,
        is_admin: bool,
        top_k: int = 0,
    ) -> dict[str, Any]:
        row = self._require_manage(source_id, user_id=user_id, is_admin=is_admin)
        spec = self.to_spec(row)
        try:
            async with asyncio.timeout(spec.timeout_seconds):
                result = await self._search_spec(spec, query, top_k=top_k or spec.top_k)
        except TimeoutError as exc:
            raise ExternalSourceError(
                "TIMEOUT", "retrieval deadline exceeded", origin=row.kind
            ) from exc
        return {
            "source_id": source_id,
            "chunks": [self._chunk_public(chunk) for chunk in result.chunks],
            "warnings": [vars(warning) for warning in result.warnings],
            "trace_ids": result.trace_ids,
        }

    async def _search_spec(
        self, spec: ExternalSourceSpec, query: str, *, top_k: int
    ) -> ExternalSearchResult:
        row = self._repo.get(spec.binding_id)
        if row is not None:
            row = self._migrate_row(row)
        if row is None:
            raise ExternalSourceError(
                "NOT_FOUND", "external knowledge source does not exist", origin=spec.kind
            )
        adapter = get_adapter(spec.kind)
        if adapter is None:
            raise ExternalSourceError(
                "INVALID_ARGUMENT", f"unsupported kind: {spec.kind}", origin=spec.kind
            )
        if not row.enabled or not row.connection_enabled:
            raise ExternalSourceError("FORBIDDEN", "source is disabled", origin=row.kind)
        if isinstance(spec.octop_identity, OctopIdentity):
            self._require_use(
                row.source_id,
                user_id=int(spec.octop_identity.user_id),
                is_admin=spec.octop_identity.is_admin,
            )
        creds = self.credentials_for(row)
        if self.auth_type(row) == "agent":
            if not isinstance(creds.get("agent_identity"), dict) or not creds["agent_identity"].get(
                "simple"
            ):
                from octop.infra.errors import ErrorCode, OctopError

                raise OctopError.localized(ErrorCode.AGENT_IDENTITY_NOT_READY)
            from octop.infra.errors import OctopError
            from octop.infra.knowledge.external.agent_identity import search_as_agent

            try:
                result = await search_as_agent(spec, creds, query, top_k=top_k)
            except (OctopError, ExternalSourceError) as exc:
                self._record_runtime_verification(
                    row, spec, creds["agent_identity"], "failure", str(exc.code)
                )
                raise
            self._record_runtime_verification(row, spec, creds["agent_identity"], "success")
            return result
        try:
            result = await adapter.search(
                replace(spec, octop_identity=None),
                creds,
                query,
                top_k=top_k,
            )
        except ExternalSourceError as exc:
            self._repo.record_check(
                row.connection_id,
                row.source_id,
                "",
                "retrieval",
                "failed",
                exc.code,
                expected_revision=row.revision,
            )
            raise
        self._repo.record_check(
            row.connection_id,
            row.source_id,
            "",
            "retrieval",
            "success",
            expected_revision=row.revision,
        )
        return result

    def _record_runtime_verification(
        self,
        row: ExternalSourceRow,
        spec: ExternalSourceSpec,
        config: dict[str, Any],
        status: str,
        code: str = "",
    ) -> None:

        identity = spec.octop_identity
        if not isinstance(identity, OctopIdentity):
            return
        agent = config.get("agents", {}).get(identity.agent_id)
        if not agent:
            return
        if agent.get("keyReference"):
            self._repo.adopt_legacy_key(
                row.connection_id,
                identity.agent_id,
                agent["externalAgentId"],
                agent["keyReference"],
            )
        self._repo.record_check(
            row.connection_id,
            row.source_id,
            identity.agent_id,
            "retrieval",
            status,
            code,
            expected_revision=row.revision,
        )
        self._repo.record_verification(
            row.connection_id,
            identity.agent_id,
            agent["externalAgentId"],
            status,
            datetime.now(UTC).isoformat(),
        )

    async def retrieve(
        self,
        *,
        query: str,
        specs: list[ExternalSourceSpec],
        limit: int = DEFAULT_TOP_K,
        octop_identity: OctopIdentity | None = None,
    ) -> dict[str, Any]:
        """Search every enabled source and fuse the results by rank."""
        if not query.strip() or not specs or limit <= 0:
            return {"chunks": [], "warnings": []}
        runtime_specs = [replace(spec, octop_identity=octop_identity) for spec in specs]
        semaphore = asyncio.Semaphore(4)

        async def search_one(spec: ExternalSourceSpec) -> ExternalSearchResult:
            try:
                async with asyncio.timeout(min(spec.timeout_seconds, MAX_TIMEOUT_SECONDS)):
                    async with semaphore:
                        return await self._search_spec(spec, query, top_k=spec.top_k)
            except TimeoutError as exc:
                raise ExternalSourceError(
                    "TIMEOUT", "retrieval deadline exceeded", origin=spec.kind
                ) from exc

        if len(runtime_specs) > 16:
            raise ExternalSourceError("INVALID_ARGUMENT", "at most 16 sources may be queried")
        from octop.infra.knowledge.external.http import pooled_requests

        async with pooled_requests():
            outcomes = await asyncio.gather(
                *[search_one(spec) for spec in runtime_specs], return_exceptions=True
            )
        groups: list[list[ExternalChunk]] = []
        warnings: list[SourceWarning] = []
        for spec, outcome in zip(runtime_specs, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                code = "UNEXPECTED"
                message = "retrieval failed"
                if isinstance(outcome, ExternalSourceError):
                    code = outcome.code
                    message = str(outcome)
                else:
                    from octop.infra.errors import OctopError

                    if isinstance(outcome, OctopError):
                        code = str(outcome.code.value)
                        message = "Agent identity is not ready"
                warnings.append(
                    SourceWarning(
                        origin=spec.kind, code=code, message=message, source_id=spec.binding_id
                    )
                )
                continue
            groups.append([replace(chunk, source_id=spec.binding_id) for chunk in outcome.chunks])
            warnings.extend(outcome.warnings)
        fused = rrf_fuse(
            groups,
            limit=limit,
            key=lambda chunk: f"{chunk.source_id}:{chunk.ref}:{chunk.dedupe_key()}",
        )
        return {
            "status": "partial"
            if warnings and fused
            else "failed"
            if warnings and len(warnings) == len(runtime_specs)
            else "ok"
            if fused
            else "empty",
            "chunks": [self._chunk_public(chunk) for chunk in fused],
            "warnings": [vars(warning) for warning in warnings],
        }

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _chunk_public(chunk: ExternalChunk) -> dict[str, Any]:
        return {
            "id": chunk.id,
            "source_id": chunk.source_id,
            "content": chunk.content,
            "score": chunk.score,
            "source": chunk.source,
            "metadata": chunk.metadata,
            "origin": chunk.origin,
            "ref": chunk.ref,
        }

    def _require_get(self, source_id: str) -> ExternalSourceRow:
        row = self._repo.get(source_id)
        if row is None:
            raise ExternalSourceError("NOT_FOUND", "external knowledge source does not exist")
        try:
            return self._migrate_row(row)
        except (InvalidToken, ValueError) as exc:
            raise ExternalSourceError(
                "INVALID_CREDENTIALS", "stored configuration requires repair"
            ) from exc

    def _require_use(self, source_id: str, *, user_id: int, is_admin: bool) -> ExternalSourceRow:
        row = self._require_get(source_id)
        if not row.connection_enabled or (
            not is_admin
            and (
                (row.owner_user_id != user_id and not row.shared)
                or (row.connection_owner_user_id != user_id and not row.connection_shared)
            )
        ):
            raise ExternalSourceError("FORBIDDEN", "source is not available")
        return row

    def _assert_target(
        self, target_type: str, target_id: str, *, user_id: int, is_admin: bool
    ) -> None:
        owner = self._repo.target_owner(target_type, target_id)
        if owner is None or (not is_admin and owner != user_id):
            raise ExternalSourceError("FORBIDDEN", "target is not available")

    def _require_manage(self, source_id: str, *, user_id: int, is_admin: bool) -> ExternalSourceRow:
        row = self._require_get(source_id)
        if not is_admin and row.owner_user_id != user_id:
            raise ExternalSourceError("FORBIDDEN", "not allowed to manage this source")
        return row

    @staticmethod
    def now_ts() -> int:
        return now_ts()
