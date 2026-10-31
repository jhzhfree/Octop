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

from octop.infra.connectors.crypto import decrypt_credentials, encrypt_credentials
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.connectors import ConnectorRepo
from octop.infra.db.repos.external_knowledge import (
    TARGET_TYPES,
    ExternalKnowledgeRepo,
    ExternalSourceRow,
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
        blob = row.credential_blob
        if row.instance_id:
            instance = self._connector_repo.get(row.instance_id)
            if instance is not None and instance.credential_blob:
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
            result["auth_type"] = self.auth_type(row)
            return result
        except Exception as exc:  # noqa: BLE001 - never leak key material
            logger.warning("external knowledge credentials unreadable for source=%s", row.source_id)
            raise ExternalSourceError(
                "INVALID_CREDENTIALS", "stored credentials could not be read", origin=row.kind
            ) from exc

    def auth_type(self, row: ExternalSourceRow) -> str:
        own = (
            decrypt_credentials(self._secret_repo, row.credential_blob)
            if row.credential_blob
            else {}
        )
        mode = own.get("auth_type")
        if mode is None:
            mode = "agent" if own.get("agent_identity", {}).get("simple") else "service"
        return self._validate_auth_type(str(mode), row.kind)

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
            spec = self.to_spec(row)
            result.append(
                {
                    **self._public_source(row, spec),
                    "can_manage": is_admin or row.owner_user_id == user_id,
                    "bindings": bindings.get(row.source_id, []),
                }
            )
        return result

    def _public_source(self, row: ExternalSourceRow, spec: ExternalSourceSpec) -> dict[str, Any]:
        """Public projection: no credentials, no key material."""
        identity_ready = False
        api_key_header = API_KEY_HEADERS[0]
        try:
            credentials = self.credentials_for(row)
            has_credentials = bool(credentials.get("api_key"))
            config = credentials.get("agent_identity", {})
            identity_ready = (
                self.auth_type(row) == "agent"
                and bool(config.get("simple"))
                and bool(config.get("agents"))
            )
            stored = str(credentials.get("api_key_header") or "")
            if stored in API_KEY_HEADERS:
                api_key_header = stored
        except ExternalSourceError:
            has_credentials = False
        return {
            "auth_type": self.auth_type(row),
            "identity_ready": identity_ready,
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
        if not row.credential_blob:
            return {}
        try:
            config = decrypt_credentials(self._secret_repo, row.credential_blob).get(
                "agent_identity", {}
            )
            return config if isinstance(config, dict) else {}
        except Exception as exc:
            raise ExternalSourceError(
                "INVALID_CREDENTIALS", "stored identity configuration could not be read"
            ) from exc

    def save_identity_config(
        self,
        row: ExternalSourceRow,
        config: dict[str, Any],
        *,
        agent_id: str | None = None,
        bound: bool = True,
    ) -> None:
        own = (
            decrypt_credentials(self._secret_repo, row.credential_blob)
            if row.credential_blob
            else {}
        )
        own["agent_identity"] = config
        blob = encrypt_credentials(self._secret_repo, own)
        saved = (
            self._repo.compare_replace_identity_binding(
                row.source_id, row.credential_blob, blob, agent_id, bound=bound
            )
            if agent_id is not None
            else self._repo.compare_replace_credentials(row.source_id, row.credential_blob, blob)
        )
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
        if kind not in SOURCE_KINDS:
            raise ExternalSourceError("INVALID_ARGUMENT", f"unsupported kind: {kind}")
        name = (display_name or "").strip()
        if not name:
            raise ExternalSourceError("INVALID_ARGUMENT", "display_name is required")
        if not (endpoint or "").strip():
            raise ExternalSourceError("INVALID_ARGUMENT", "endpoint is required")
        if timeout_ms <= 0 or timeout_ms > int(MAX_TIMEOUT_SECONDS * 1000):
            raise ExternalSourceError("INVALID_ARGUMENT", "timeout_ms must be between 1 and 60000")
        if top_k < 1 or top_k > MAX_TOP_K:
            raise ExternalSourceError("INVALID_ARGUMENT", f"top_k must be 1-{MAX_TOP_K}")
        if not 0.0 <= min_score <= 1.0:
            raise ExternalSourceError("INVALID_ARGUMENT", "min_score must be within [0, 1]")
        self._validate_auth_type(auth_type, kind)
        payload: dict[str, Any] = {"auth_type": auth_type}
        blob: bytes | None = None
        if instance_id:
            if self._connector_repo.get(instance_id) is None:
                raise ExternalSourceError("INVALID_ARGUMENT", "connector instance does not exist")
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
        )
        row = self._require_get(source_id)
        return self._public_source(row, self.to_spec(row))

    def update_source(
        self,
        source_id: str,
        *,
        user_id: int,
        is_admin: bool,
        **fields: Any,
    ) -> dict[str, Any]:
        row = self._require_manage(source_id, user_id=user_id, is_admin=is_admin)
        if self.identity_config(row).get("simple") and (
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
            merged = {**previous, "auth_type": mode}
            if mode == "service":
                merged.pop("agent_identity", None)
            if new_key and not row.instance_id:
                merged["api_key"] = new_key
            if scheme is not None:
                merged["api_key_header"] = scheme
            blob = encrypt_credentials(self._secret_repo, merged)
            replace_credentials = True
            if "agent_identity" in previous or auth_type is not None:
                if not self._repo.compare_replace_credentials(source_id, row.credential_blob, blob):
                    from octop.infra.errors import ErrorCode, OctopError

                    raise OctopError.localized(ErrorCode.AGENT_IDENTITY_CONFLICT)
                replace_credentials = False
                blob = None
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
            tool_arguments=None if tool_arguments is None else _dump_tool_arguments(tool_arguments),
            enabled=None if enabled is None else bool(enabled),
            shared=None if shared is None else bool(shared),
            credential_blob=blob,
            replace_credentials=replace_credentials,
        )
        updated = self._require_get(source_id)
        return self._public_source(updated, self.to_spec(updated))

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
        self._require_manage(source_id, user_id=user_id, is_admin=is_admin)
        if target_type not in TARGET_TYPES:
            raise ExternalSourceError("INVALID_ARGUMENT", f"unsupported target type: {target_type}")
        target = (target_id or "").strip()
        if not target:
            raise ExternalSourceError("INVALID_ARGUMENT", "target_id is required")
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
        self._require_manage(source_id, user_id=user_id, is_admin=is_admin)
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
            result = await adapter.probe(spec, creds)
        except ExternalSourceError as exc:
            return {"ok": False, "code": exc.code, "message": str(exc)}
        except Exception:  # noqa: BLE001 - upstreams are untrusted
            logger.warning("external knowledge probe failed source=%s", source_id, exc_info=True)
            return {"ok": False, "code": "UNEXPECTED", "message": "probe failed"}
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
        result = await self._search_spec(spec, query, top_k=top_k or spec.top_k)
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
        if row is None:
            raise ExternalSourceError(
                "NOT_FOUND", "external knowledge source does not exist", origin=spec.kind
            )
        adapter = get_adapter(spec.kind)
        if adapter is None:
            raise ExternalSourceError(
                "INVALID_ARGUMENT", f"unsupported kind: {spec.kind}", origin=spec.kind
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
            except (OctopError, ExternalSourceError):
                self._record_runtime_verification(row, spec, creds["agent_identity"], "failure")
                raise
            self._record_runtime_verification(row, spec, creds["agent_identity"], "success")
            return result
        return await adapter.search(
            replace(spec, octop_identity=None),
            creds,
            query,
            top_k=top_k,
        )

    def _record_runtime_verification(
        self, row: ExternalSourceRow, spec: ExternalSourceSpec, config: dict[str, Any], status: str
    ) -> None:
        from octop.infra.errors import OctopError

        identity = spec.octop_identity
        if not isinstance(identity, OctopIdentity):
            return
        agent = config.get("agents", {}).get(identity.agent_id)
        if not agent:
            return
        agent["lastVerifiedAt"] = datetime.now(UTC).isoformat()
        agent["lastVerificationStatus"] = status
        try:
            # CAS prevents an in-flight search from restoring a removed or rotated binding.
            self.save_identity_config(row, config)
        except OctopError:
            logger.info("Identity state changed during retrieval for source=%s", row.source_id)

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
        runtime_specs = [
            replace(spec, octop_identity=octop_identity)
            if spec.kind in {"akb-rest", "akb-mcp"}
            else spec
            for spec in specs
        ]
        outcomes = await asyncio.gather(
            *[self._search_spec(spec, query, top_k=spec.top_k) for spec in runtime_specs],
            return_exceptions=True,
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
                    logger.warning(
                        "external retrieval failed source=%s", spec.binding_id, exc_info=outcome
                    )
                warnings.append(SourceWarning(origin=spec.kind, code=code, message=message))
                continue
            groups.append(outcome.chunks)
            warnings.extend(outcome.warnings)
        fused = rrf_fuse(
            groups, limit=limit, key=lambda chunk: f"{chunk.origin}:{chunk.dedupe_key()}"
        )
        return {
            "chunks": [self._chunk_public(chunk) for chunk in fused],
            "warnings": [vars(warning) for warning in warnings],
        }

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _chunk_public(chunk: ExternalChunk) -> dict[str, Any]:
        return {
            "id": chunk.id,
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
        return row

    def _require_manage(self, source_id: str, *, user_id: int, is_admin: bool) -> ExternalSourceRow:
        row = self._require_get(source_id)
        if not is_admin and row.owner_user_id != user_id:
            raise ExternalSourceError("FORBIDDEN", "not allowed to manage this source")
        return row

    @staticmethod
    def now_ts() -> int:
        return now_ts()
